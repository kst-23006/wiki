import re
import streamlit as st
from wikipedia_extractor_core import (
    WikipediaExtractor,
    format_txt,
    numbered_filename,
    safe_filename,
)

st.set_page_config(page_title="Wikipedia Extractor", layout="wide")

# Initialize session state keys only if not already present
_DEFAULT_SESSION_STATE = {
    "topic": None,
    "article": None,
    "titles": [],
    "selected": [],
    "sections": [],
    "saved_path": None,
    "pending_overwrite": None,
    "pending_filename": None,
    "downloaded_filenames": [],
}

for key, default_value in _DEFAULT_SESSION_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = (
            list(default_value) if isinstance(default_value, list) else default_value
        )

# Recover from corrupt session_state where article is missing but derived state remains
if st.session_state.article is None and (
    bool(st.session_state.titles) or bool(st.session_state.sections)
):
    st.session_state.titles = []
    st.session_state.selected = []
    st.session_state.sections = []
    st.session_state.downloaded_filenames = []
    st.rerun()


def _get_section_titles(article: WikipediaExtractor) -> list[str]:
    """Return top-level section title strings from the article."""
    if hasattr(article, "section_titles"):
        return article.section_titles()
    return [title for _, title in article.get_section_titles()]


def _parse_content_to_blocks(content: str) -> list[dict]:
    """Convert plain-text section output into typed content blocks for rendering."""
    if (
        not content
        or not content.strip()
        or content.strip() == "[No extractable text content in this section.]"
    ):
        return []

    blocks: list[dict] = []
    for chunk in content.split("\n\n"):
        stripped_chunk = chunk.strip()
        if not stripped_chunk:
            continue

        # Subheading block: "--- Subheading ---"
        subheading_match = re.fullmatch(r"---\s*(.+?)\s*---", stripped_chunk)
        if subheading_match:
            blocks.append({"type": "subheading", "text": subheading_match.group(1)})
            continue

        lines = chunk.splitlines()

        # Table block: starts with "[Table" followed by pipe-delimited rows
        if lines[0].strip().startswith("[Table"):
            rows: list[list[str]] = []
            for line in lines[1:]:
                stripped_line = line.strip()
                if stripped_line.startswith("|") and stripped_line.endswith("|"):
                    inner = stripped_line[1:-1]
                    if inner.startswith(" "):
                        inner = inner[1:]
                    if inner.endswith(" "):
                        inner = inner[:-1]
                    cells = [cell.strip() for cell in inner.split(" | ")] if inner else [""]
                    rows.append(cells)
            if rows:
                max_cols = max(len(row) for row in rows)
                normalized_rows = [row + [""] * (max_cols - len(row)) for row in rows]
                blocks.append({"type": "table", "rows": normalized_rows})
            continue

        # List block: every line starts with "-", "*", or "<number>."
        if all(re.match(r"^\s*(?:[-*]|\d+\.)\s+", line) for line in lines):
            ordered = bool(re.match(r"^\s*\d+\.\s+", lines[0]))
            items = [
                re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line).strip()
                for line in lines
                if line.strip()
            ]
            blocks.append({"type": "list", "ordered": ordered, "items": items})
            continue

        # Default: paragraph block
        blocks.append({"type": "paragraph", "text": stripped_chunk})

    return blocks


def _extract_section(article: WikipediaExtractor, title: str) -> dict:
    """Extract a single section by title into {'title': str, 'blocks': list[dict]}."""
    if hasattr(article, "extract"):
        return article.extract(title)

    matching_index = next(
        (idx for idx, sec_title in article.get_section_titles() if sec_title == title),
        None,
    )
    if matching_index is None:
        return {"title": title, "blocks": []}

    extracted = article.extract_sections([matching_index])
    if not extracted:
        return {"title": title, "blocks": []}

    section_data = extracted[0]
    return {
        "title": section_data["title"],
        "blocks": _parse_content_to_blocks(section_data.get("content", "")),
    }


def _record_download(filename: str) -> None:
    """Append the downloaded filename to session state."""
    st.session_state.downloaded_filenames.append(filename)


# Sidebar
with st.sidebar:
    st.header("Controls")
    if st.button("Start Over"):
        st.session_state.clear()
        st.rerun()

# Main area
st.title("Wikipedia Article Extractor")

with st.expander("How to use this app"):
    st.markdown(
        "1. **Enter a topic** and click **Search** to load a Wikipedia article.\n"
        "2. **Select sections** you want from the dropdown menu.\n"
        "3. **Click Extract** to render a cleaned preview of the selected sections.\n"
        "4. **Click Download** to save the formatted extract as a `.txt` file."
    )

# Progress indicator above the search form
progress_steps = [
    ("Search", bool(st.session_state.topic)),
    ("Select", bool(st.session_state.titles)),
    ("Extract", bool(st.session_state.sections)),
    ("Download", bool(st.session_state.downloaded_filenames)),
]
progress_cols = st.columns(4)
for col, (step_label, completed) in zip(progress_cols, progress_steps):
    checkmark = "✅" if completed else "⬜"
    col.caption(f"{checkmark} {step_label}")

# Stage 1 - Search
with st.form(key="search_form"):
    topic_input = st.text_input("Enter a topic", value=st.session_state.topic or "")
    submitted = st.form_submit_button("Search")

if submitted:
    st.session_state.topic = topic_input
    article = WikipediaExtractor(topic_input)
    st.session_state.article = article

    if article.error:
        st.error(article.error)
        st.session_state.titles = []
        st.session_state.selected = []
        st.session_state.sections = []
    else:
        st.success(f"Loaded article: {article.title}")
        st.write(article.url)
        titles = _get_section_titles(article)
        if len(titles) <= 1:
            st.warning("This article has no usable sections to select.")
            st.session_state.titles = []
        else:
            st.session_state.titles = titles
        st.session_state.selected = []
        st.session_state.sections = []

# Stage 2 - Select & Stage 3 - Extract
if (
    st.session_state.article is not None
    and not st.session_state.article.error
    and len(st.session_state.titles) > 1
):
    st.session_state.selected = [
        item for item in st.session_state.selected if item in st.session_state.titles
    ]
    st.multiselect(
        "Select sections",
        options=st.session_state.titles,
        key="selected",
    )

    if st.button("Extract"):
        if not st.session_state.selected:
            st.warning("Please select at least one section to extract.")
        else:
            article = st.session_state.article
            st.session_state.sections = [
                _extract_section(article, title)
                for title in st.session_state.selected
            ]

# Stage 4 - Preview & Download
if (
    st.session_state.article is not None
    and not st.session_state.article.error
    and st.session_state.sections
):
    with st.container(border=True):
        for section in st.session_state.sections:
            st.markdown(f"### {section['title']}")
            blocks = section.get("blocks", [])

            if not blocks:
                st.caption("(no extractable content)")
                continue

            for block in blocks:
                block_type = block.get("type")
                if block_type == "paragraph":
                    st.write(block["text"])
                elif block_type == "subheading":
                    st.markdown(f"#### {block['text']}")
                elif block_type == "list":
                    marker = "1." if block.get("ordered") else "-"
                    for item in block.get("items", []):
                        st.markdown(f"{marker} {item}")
                elif block_type == "table":
                    st.dataframe(block["rows"])

    article = st.session_state.article
    proposed_filename = safe_filename(article.title) + ".txt"
    download_filename = numbered_filename(
        proposed_filename, st.session_state.downloaded_filenames
    )
    if proposed_filename in st.session_state.downloaded_filenames:
        st.warning(
            "You already downloaded a file with this name in this session. "
            f"Downloading again will save it as '{download_filename}'."
        )

    st.download_button(
        label="Download .txt",
        data=format_txt(article.title, article.url, st.session_state.sections),
        file_name=download_filename,
        mime="text/plain",
        key="download_btn",
        on_click=_record_download,
        args=(download_filename,),
    )

st.caption("Content from Wikipedia, CC BY-SA 4.0.")

# Debugging readout
st.write(st.session_state)