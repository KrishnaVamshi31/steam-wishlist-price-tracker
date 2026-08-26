"""Steam wishlist price tracker — dashboard entry point.

Run with:  streamlit run streamlit_app.py
"""
import streamlit as st

st.set_page_config(
    page_title="Wishlist price tracker",
    page_icon=":material/sports_esports:",
    layout="wide",
)

pages = [
    st.Page(
        "app_pages/overview.py",
        title="Overview",
        icon=":material/dashboard:",
        default=True,
    ),
    st.Page("app_pages/chat.py", title="Ask", icon=":material/forum:"),
    st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:"),
]

st.navigation(pages).run()
