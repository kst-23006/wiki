import streamlit as st

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

# Sidebar
with st.sidebar:
    st.header("Controls")
    if st.button("Start Over"):
        st.session_state.clear()
        st.rerun()

# Main area
st.title("Wikipedia Article Extractor")

with st.form(key="search_form"):
    st.text_input("Enter a topic")
    st.form_submit_button("Search")

# Placeholder sections (empty for now)
article_info_placeholder = st.empty()
section_multiselect_placeholder = st.empty()
extract_button_placeholder = st.empty()
preview_placeholder = st.empty()
download_button_placeholder = st.empty()

# Debugging readout
st.write(st.session_state)