"""
Core logic layer for the Wikipedia Article Extractor Streamlit web app.

Contains module-level constants, pure helper functions, and the
WikipediaExtractor class with zero console I/O or disk-writing side effects.
"""

import os
import re
import time
from collections.abc import Iterable
from datetime import datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, Tag

WIKI_BASE = "https://en.wikipedia.org/wiki/"
BASE_URL = WIKI_BASE
API_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
REQUEST_TIMEOUT = 10
REQUEST_DELAY_SECONDS = 0.5

HEADERS = {
    "User-Agent": (
        "ISOM3400WikipediaExtractor/3.0 "
        "(Educational Python Project; https://en.wikipedia.org/)"
    )
}

# Section titles to skip during parsing (empty by default; "Contents" is filtered in _extract_h2_title).
SKIP_SECTIONS: set[str] = set()

# Page furniture and templates that usually do not belong in section text.
SKIP_CLASSES = {
    "navbox", "vertical-navbox", "sidebar", "sistersitebox",
    "ambox", "tmbox", "cmbox", "ombox", "fmbox", "dmbox",
    "hatnote", "shortdescription", "mw-editsection", "toc",
    "reflist", "reference", "noprint", "nomobile", "catlinks",
    "mw-empty-elt", "printfooter", "authority-control",
    "infobox", "infobox-subbox", "metadata",
}
EXCLUDED_CLASSES = SKIP_CLASSES


def to_title(topic: str) -> str:
    """Normalize a topic string into title case for URL fallback lookup."""
    if not isinstance(topic, str):
        return ""
    return topic.strip().title()


def safe_filename(title: str, fallback: str = "wikipedia_article") -> str:
    """Create a meaningful, filesystem-safe filename stem (without .txt) from a title."""
    base = title if isinstance(title, str) and title.strip() else fallback
    sanitized = re.sub(r'[\\/*?:"<>|]', "", base)
    sanitized = re.sub(r"\s+", "_", sanitized.strip()).strip(". ")
    if sanitized.lower().endswith(".txt"):
        sanitized = sanitized[:-4]
    if not sanitized:
        sanitized = fallback
    return f"{sanitized}_extract"


def numbered_filename(
    filename: str,
    existing_filenames: Iterable[str] | None = None,
) -> str:
    """Return a unique filename against existing_filenames, adding (2), (3), etc. on collision."""
    existing = set(existing_filenames) if existing_filenames else set()
    if filename not in existing:
        return filename

    base, extension = os.path.splitext(filename)
    if not extension:
        extension = ".txt"

    counter = 2
    candidate = f"{base} ({counter}){extension}"
    while candidate in existing:
        counter += 1
        candidate = f"{base} ({counter}){extension}"
    return candidate


def _format_blocks_to_text(blocks: list[dict]) -> str:
    """Convert structured section blocks back into formatted plain text."""
    if not blocks:
        return "[No extractable text content in this section.]"

    rendered_blocks: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "paragraph":
            text = block.get("text", "").strip()
            if text:
                rendered_blocks.append(text)
        elif block_type == "subheading":
            text = block.get("text", "").strip()
            if text:
                rendered_blocks.append(f"--- {text} ---")
        elif block_type == "list":
            items = block.get("items", [])
            if items:
                if block.get("ordered"):
                    lines = [f"  {idx}. {item}" for idx, item in enumerate(items, start=1)]
                else:
                    lines = [f"  - {item}" for item in items]
                rendered_blocks.append("\n".join(lines))
        elif block_type == "table":
            rows = block.get("rows", [])
            if rows:
                table_lines = ["[Table]"]
                for row in rows:
                    table_lines.append("  | " + " | ".join(str(cell) for cell in row) + " |")
                rendered_blocks.append("\n".join(table_lines))

    if not rendered_blocks:
        return "[No extractable text content in this section.]"
    return "\n\n".join(rendered_blocks)


def format_txt(
    article_title: str,
    article_url: str,
    sections: list[dict],
) -> str:
    """Format extracted sections and required source/licence metadata as a single string."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    separator = "=" * 72
    thin_separator = "-" * 72

    header_lines = [
        separator,
        f"WIKIPEDIA ARTICLE EXTRACT: {article_title}",
        separator,
        f"Source URL          : {article_url}",
        f"Extraction Timestamp: {timestamp}",
        "Licence             : Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0)",
        "Licence URL         : https://creativecommons.org/licenses/by-sa/4.0/",
        separator,
        "",
    ]

    body_lines: list[str] = []
    for position, section in enumerate(sections, start=1):
        section_index = section.get("index", position)
        section_title = section.get("title", "")
        if "content" in section:
            section_body = section["content"]
        else:
            section_body = _format_blocks_to_text(section.get("blocks", []))

        body_lines.extend([
            thin_separator,
            f"SECTION {section_index}: {section_title.upper()}",
            thin_separator,
            section_body,
            "",
        ])

    return "\n".join(header_lines + body_lines)


class WikipediaExtractor:
    """Fetch, parse, and extract sections from one English Wikipedia article."""

    BASE_URL = WIKI_BASE
    API_SEARCH_URL = API_SEARCH_URL
    REQUEST_TIMEOUT = REQUEST_TIMEOUT
    REQUEST_DELAY_SECONDS = REQUEST_DELAY_SECONDS
    HEADERS = HEADERS
    EXCLUDED_CLASSES = SKIP_CLASSES

    def __init__(self, topic: str):
        self.error: str | None = None
        # Guard against an unexpected non-string value instead of crashing.
        self.topic = topic.strip() if isinstance(topic, str) else ""
        self.title: str | None = None
        self.url = ""
        self.soup: BeautifulSoup | None = None
        # Each item: {"title": str, "elements": list[BeautifulSoup Tag]}
        self.sections: list[dict] = []
        self._has_made_request = False
        self._api_error = ""

        fetch_ok, _ = self.fetch()
        if fetch_ok:
            self.parse_sections()

    def _set_error(self, message: str) -> tuple[bool, str]:
        """Record an error message on the instance and clear any partial title."""
        self.error = message
        self.title = None
        return False, message

    def _get(self, url: str, **kwargs):
        """Send a request, spacing multiple requests in one search action."""
        if self._has_made_request:
            time.sleep(self.REQUEST_DELAY_SECONDS)
        self._has_made_request = True
        kwargs.setdefault("headers", self.HEADERS)
        kwargs.setdefault("timeout", self.REQUEST_TIMEOUT)
        return requests.get(url, **kwargs)

    def fetch(self) -> tuple[bool, str]:
        """Fetch the article page and validate that it is a real article."""
        self.error = None
        self.title = None

        if not self.topic:
            return self._set_error("Topic cannot be empty. Please enter a valid topic.")
        if not any(character.isalnum() for character in self.topic):
            return self._set_error("Invalid topic. Please include at least one letter or number.")

        # Try the entered capitalization first, then a title-cased URL if needed.
        slug_candidates = [
            self.topic.replace(" ", "_"),
            to_title(self.topic).replace(" ", "_"),
        ]
        unique_slugs = list(dict.fromkeys(slug_candidates))
        response = None

        for slug in unique_slugs:
            candidate_url = f"{self.BASE_URL}{quote(slug, safe='()_:,')}"
            try:
                candidate_response = self._get(candidate_url)
                if candidate_response.status_code == 200:
                    response = candidate_response
                    break
                if candidate_response.status_code == 404:
                    continue
                candidate_response.raise_for_status()
            except requests.exceptions.Timeout:
                return self._set_error("Network error: The request to Wikipedia timed out.")
            except requests.exceptions.ConnectionError:
                return self._set_error(
                    "Network error: Unable to connect to Wikipedia. Check your internet connection."
                )
            except requests.exceptions.HTTPError as http_error:
                return self._set_error(f"HTTP error occurred: {http_error}")
            except requests.exceptions.RequestException as request_error:
                return self._set_error(f"Network request error: {request_error}")

        # OpenSearch can resolve capitalization differences when direct URLs fail.
        if response is None:
            resolved_title = self._resolve_topic_via_api(self.topic)
            if not resolved_title and self._api_error:
                return self._set_error(self._api_error)
            if resolved_title:
                candidate_url = (
                    f"{self.BASE_URL}"
                    f"{quote(resolved_title.replace(' ', '_'), safe='()_:,')}"
                )
                try:
                    candidate_response = self._get(candidate_url)
                    if candidate_response.status_code == 200:
                        response = candidate_response
                    elif candidate_response.status_code != 404:
                        candidate_response.raise_for_status()
                except requests.exceptions.Timeout:
                    return self._set_error("Network error: The article lookup timed out.")
                except requests.exceptions.ConnectionError:
                    return self._set_error(
                        "Network error: Unable to connect during article lookup."
                    )
                except requests.exceptions.HTTPError as http_error:
                    return self._set_error(f"HTTP error during article lookup: {http_error}")
                except requests.exceptions.RequestException as request_error:
                    return self._set_error(f"Network error during article lookup: {request_error}")

        if response is None:
            return self._set_error(
                f"Article '{self.topic}' could not be found on Wikipedia "
                "(404 Not Found). Please check the spelling or try a more specific topic."
            )

        self.url = response.url
        try:
            # html.parser is built into Python; lxml is optional.
            self.soup = BeautifulSoup(response.text, "html.parser")
        except Exception as parse_error:
            return self._set_error(f"Failed to parse HTML content: {parse_error}")

        heading_tag = self.soup.select_one("h1#firstHeading")
        if not heading_tag:
            return self._set_error("Unexpected page structure: Could not locate the article title.")

        page_title = heading_tag.get_text(strip=True)

        if self.soup.select_one("div.noarticletext"):
            return self._set_error(f"No Wikipedia article exists for '{self.topic}'.")

        # Ask Wikipedia whether THIS page has the disambiguation page property.
        # Do not search the whole HTML for .mw-disambig: that class can appear
        # on links pointing to disambiguation pages, causing false positives.
        if self._is_disambiguation_page(page_title):
            return self._set_error(
                f"'{page_title}' is a disambiguation page. "
                "Please enter a more specific topic (for example, a full person or company name)."
            )

        self.title = page_title
        return True, f"Successfully loaded article: '{self.title}'"

    def _is_disambiguation_page(self, page_title: str | None = None) -> bool:
        """Return whether Wikipedia marks the current article as disambiguation.

        The MediaWiki pageprops API reports a ``disambiguation`` property only
        when the current page itself is a disambiguation page. This avoids
        mistaking an ordinary link to a disambiguation page for the page itself.
        If the optional check fails because of a temporary API/network issue,
        allow the article to continue rather than incorrectly rejecting it.
        """
        lookup_title = page_title if page_title is not None else (self.title or "")
        params = {
            "action": "query",
            "prop": "pageprops",
            "titles": lookup_title,
            "redirects": 1,
            "format": "json",
        }
        try:
            response = self._get(self.API_SEARCH_URL, params=params)
            response.raise_for_status()
            data = response.json()
            pages = data.get("query", {}).get("pages", {})
            return any(
                "disambiguation" in page.get("pageprops", {})
                for page in pages.values()
                if isinstance(page, dict)
            )
        except (
            requests.exceptions.RequestException,
            ValueError,
            TypeError,
            AttributeError,
        ):
            # A failed optional check should not create a false-positive error.
            return False

    def _resolve_topic_via_api(self, query: str) -> str | None:
        """Resolve an exact case-insensitive title using Wikipedia OpenSearch."""
        params = {
            "action": "opensearch",
            "search": query,
            "limit": 5,
            "namespace": 0,
            "format": "json",
        }
        self._api_error = ""
        try:
            response = self._get(self.API_SEARCH_URL, params=params, timeout=8)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
                for candidate in data[1]:
                    if isinstance(candidate, str) and candidate.casefold() == query.casefold():
                        return candidate
        except requests.exceptions.Timeout:
            self._api_error = "Network error: Wikipedia's search service timed out."
        except requests.exceptions.ConnectionError:
            self._api_error = "Network error: Unable to connect to Wikipedia's search service."
        except requests.exceptions.HTTPError as http_error:
            self._api_error = f"HTTP error from Wikipedia's search service: {http_error}"
        except requests.exceptions.RequestException as request_error:
            self._api_error = f"Network error from Wikipedia's search service: {request_error}"
        except (ValueError, TypeError):
            self._api_error = (
                "Wikipedia's search service returned an unexpected response. Please try again."
            )
        return None

    def parse_sections(self) -> tuple[bool, str]:
        """Find the article's top-level sections, including the lead section."""
        if self.soup is None:
            return self._set_error("No HTML document loaded to parse.")

        try:
            content_root = self.soup.select_one("div#mw-content-text")
            if not content_root:
                return self._set_error(
                    "Parsing error: Could not find the 'mw-content-text' container."
                )

            # Prefer direct children, but retain a descendant fallback for layout changes.
            parser_outputs = content_root.find_all(
                "div", class_="mw-parser-output", recursive=False
            )
            if not parser_outputs:
                parser_outputs = content_root.select("div.mw-parser-output")

            main_output = None
            for output in parser_outputs:
                if output.find(["p", "h2"]):
                    main_output = output
                    break

            if not main_output:
                return self._set_error("Parsing error: Could not locate the main article content.")

            self.sections = []
            current_section = {
                "title": "Introduction (Lead Section)",
                "elements": [],
            }

            # Some MediaWiki pages wrap sections in <section> elements.
            top_nodes = []
            for child in main_output.children:
                if not isinstance(child, Tag):
                    continue
                if child.name == "section":
                    top_nodes.extend(
                        sub for sub in child.children if isinstance(sub, Tag)
                    )
                else:
                    top_nodes.append(child)

            for node in top_nodes:
                h2_title = self._extract_h2_title(node)
                if h2_title:
                    if (
                        current_section["elements"]
                        or current_section["title"] != "Introduction (Lead Section)"
                    ):
                        self.sections.append(current_section)
                    current_section = {"title": h2_title, "elements": []}
                else:
                    current_section["elements"].append(node)

            if (
                current_section["elements"]
                or current_section["title"] != "Introduction (Lead Section)"
            ):
                self.sections.append(current_section)

            # Do not show an empty lead section if it contains no extractable text.
            if (
                self.sections
                and self.sections[0]["title"] == "Introduction (Lead Section)"
                and not self._extract_section_body(self.sections[0]).strip()
            ):
                self.sections.pop(0)

            if not self.sections:
                return self._set_error("No readable sections could be found in this article.")

            return True, f"Found {len(self.sections)} section(s)."

        except Exception as error:
            return self._set_error(f"Unexpected parsing failure: {error}")

    def _extract_h2_title(self, node: Tag) -> str | None:
        """Identify a level-two heading in either older or modern MediaWiki HTML."""
        h2_tag = None
        if node.name == "h2":
            h2_tag = node
        elif node.name == "div" and "mw-heading2" in node.get("class", []):
            h2_tag = node.select_one("h2")

        if not h2_tag or h2_tag.get("id") == "mw-toc-heading":
            return None

        title_parts = []
        for element in h2_tag.children:
            if isinstance(element, Tag) and "mw-editsection" in element.get("class", []):
                continue
            title_parts.append(element.get_text() if isinstance(element, Tag) else str(element))

        title_text = " ".join("".join(title_parts).split()).strip()
        if not title_text or title_text.casefold() == "contents":
            return None
        if title_text.casefold() in {s.casefold() for s in SKIP_SECTIONS}:
            return None
        return title_text

    def get_section_titles(self) -> list[tuple[int, str]]:
        """Return (1-based index, section title) pairs for section selection."""
        return [
            (index, section["title"])
            for index, section in enumerate(self.sections, start=1)
        ]

    def extract_sections(self, indices: list[int]) -> list[dict[str, str]]:
        """Extract the selected sections as formatted plain text."""
        extracted_data = []
        for index in indices:
            if isinstance(index, int) and 1 <= index <= len(self.sections):
                section = self.sections[index - 1]
                body_text = self._extract_section_body(section)
                if not body_text.strip():
                    body_text = "[No extractable text content in this section.]"
                extracted_data.append({
                    "index": index,
                    "title": section["title"],
                    "content": body_text,
                })
        return extracted_data

    def _is_excluded_tag(self, tag: Tag) -> bool:
        """Skip scripts, references, navigation, and other page furniture."""
        if tag.name in {"style", "script", "noscript", "link", "meta", "sup"}:
            return True
        if tag.get("role") in {"navigation", "note"}:
            return True
        return bool(set(tag.get("class", [])).intersection(self.EXCLUDED_CLASSES))

    def _clean_text(self, tag: Tag) -> str:
        """Remove citation/edit markup and return readable text from one element."""
        # Parse a copy so cleaning never mutates the stored article DOM.
        clone = BeautifulSoup(str(tag), "html.parser")
        for unwanted in clone.find_all(["sup", "style", "script"]):
            unwanted.decompose()
        for edit_span in clone.select("span.mw-editsection"):
            edit_span.decompose()

        raw_text = clone.get_text(separator=" ", strip=True)
        cleaned = re.sub(
            r"\[\s*(?:\d+|[a-zA-Z]|note\s*\d+|citation needed|"
            r"clarification needed|update|when\?|who\?)\s*\]",
            "",
            raw_text,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"\s+([,.;:?!])", r"\1", cleaned)
        return cleaned.strip()

    def _extract_section_body(self, section: dict) -> str:
        """Traverse a section and format its supported content blocks."""
        output_blocks = []
        for element in section["elements"]:
            self._process_node(element, output_blocks)
        return "\n\n".join(block for block in output_blocks if block.strip())

    def _process_node(self, node: Tag, output_blocks: list[str]) -> None:
        """Recursively process headings, paragraphs, lists, and tables."""
        if not isinstance(node, Tag) or self._is_excluded_tag(node):
            return

        # Subheadings in older MediaWiki markup.
        if node.name in {"h3", "h4", "h5", "h6"}:
            heading_text = self._clean_text(node)
            if heading_text:
                output_blocks.append(f"--- {heading_text} ---")
            return

        # Subheadings in modern MediaWiki heading wrappers.
        if node.name == "div" and any(
            class_name in node.get("class", [])
            for class_name in ("mw-heading3", "mw-heading4", "mw-heading5", "mw-heading6")
        ):
            heading_tag = node.find(["h3", "h4", "h5", "h6"])
            if heading_tag:
                heading_text = self._clean_text(heading_tag)
                if heading_text:
                    output_blocks.append(f"--- {heading_text} ---")
            return

        if node.name in {"p", "blockquote"}:
            paragraph_text = self._clean_text(node)
            if paragraph_text:
                output_blocks.append(paragraph_text)
            return

        if node.name in {"ul", "ol"}:
            list_lines = self._format_list(node, indent_level=0)
            if list_lines:
                output_blocks.append("\n".join(list_lines))
            return

        if node.name == "dl":
            description_lines = []
            for item in node.find_all(["dt", "dd"], recursive=False):
                item_text = self._clean_text(item)
                if item_text:
                    prefix = "* " if item.name == "dt" else "  - "
                    description_lines.append(f"{prefix}{item_text}")
            if description_lines:
                output_blocks.append("\n".join(description_lines))
            return

        if node.name == "table":
            table_text = self._format_table(node)
            if table_text:
                output_blocks.append(table_text)
            return

        # Recurse through common block/container wrappers, including figure.
        if node.name in {"div", "section", "article", "main", "figure", "center"}:
            for child in node.children:
                if isinstance(child, Tag):
                    self._process_node(child, output_blocks)

    def _format_list(self, list_tag: Tag, indent_level: int = 0) -> list[str]:
        """Preserve ordered/unordered list items and nested-list indentation."""
        if self._is_excluded_tag(list_tag):
            return []

        lines = []
        is_ordered = list_tag.name == "ol"
        indent = "  " * indent_level
        counter = 1

        for list_item in list_tag.find_all("li", recursive=False):
            if self._is_excluded_tag(list_item):
                continue

            # Work on a copy and remove nested lists before extracting this item.
            item_clone = BeautifulSoup(str(list_item), "html.parser").find("li")
            if not item_clone:
                continue
            for nested_list in item_clone.find_all(["ul", "ol"]):
                nested_list.decompose()

            item_text = self._clean_text(item_clone)
            if item_text:
                prefix = f"{counter}." if is_ordered else "-"
                lines.append(f"{indent}  {prefix} {item_text}")
                counter += 1

            # Process only immediate nested lists; deeper levels recurse naturally.
            for child_list in list_item.find_all(["ul", "ol"], recursive=False):
                lines.extend(self._format_list(child_list, indent_level + 1))

        return lines

    def _format_table(self, table_tag: Tag) -> str:
        """Preserve table rows and cell positions, including empty cells."""
        if self._is_excluded_tag(table_tag):
            return ""

        rows_output = []
        caption = table_tag.find("caption")
        if caption:
            caption_text = self._clean_text(caption)
            rows_output.append(f"[Table: {caption_text}]" if caption_text else "[Table]")
        else:
            rows_output.append("[Table]")

        extracted_row_count = 0
        for row in table_tag.find_all("tr"):
            # Do not accidentally include rows belonging to a nested table.
            if row.find_parent("table") is not table_tag or self._is_excluded_tag(row):
                continue

            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                continue

            # Keep empty cells as empty strings so the table's column positions remain clear.
            cell_texts = [self._clean_text(cell) for cell in cells]
            if any(cell_texts):
                rows_output.append("  | " + " | ".join(cell_texts) + " |")
                extracted_row_count += 1

        if extracted_row_count == 0:
            return ""
        return "\n".join(rows_output)

    def get_default_filename(self) -> str:
        """Create a meaningful, filesystem-safe .txt filename from the article title."""
        return f"{safe_filename(self.title or self.topic)}.txt"