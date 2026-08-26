"""Ask questions about your wishlist in plain English."""
import streamlit as st

from tracker import chat as brain
from tracker import config

CFG = config.load()

TOOL_LABEL = {
    "query_database": "Queried the price database",
    "get_buy_advice": "Ran the buy-or-wait model",
    "get_sale_calendar": "Checked the sale calendar",
}

STARTERS = [
    "What's worth buying right now?",
    "Which of my games are most expensive?",
    "When is the next big Steam sale?",
    "Should I wait on Elden Ring?",
]

st.title("Ask about your wishlist")

if not brain.configured():
    st.warning(
        "Chat needs an Anthropic API key. Add one in **Settings → Connections**.",
        icon=":material/key:",
    )
    st.page_link(
        "app_pages/settings.py", label="Open settings", icon=":material/settings:"
    )
    st.stop()

# display  = what the user sees (plain text turns)
# api      = the full history including tool_use / tool_result blocks
st.session_state.setdefault("chat_display", [])
st.session_state.setdefault("chat_api", [])

with st.sidebar:
    if st.button("Clear conversation", icon=":material/delete_sweep:", width="stretch"):
        st.session_state.chat_display = []
        st.session_state.chat_api = []
        st.rerun()
    st.caption(f"Model: {brain.MODEL}")

if not st.session_state.chat_display:
    st.caption("Try one of these, or ask anything about your tracked games.")
    cols = st.columns(2)
    for i, starter in enumerate(STARTERS):
        with cols[i % 2]:
            if st.button(starter, width="stretch", key=f"starter_{i}"):
                st.session_state.pending_text = starter
                st.rerun()

for turn in st.session_state.chat_display:
    with st.chat_message(turn["role"]):
        if turn.get("tools"):
            with st.expander(f"Used {len(turn['tools'])} tool call(s)"):
                for tool in turn["tools"]:
                    st.markdown(f"**{tool['label']}**")
                    if tool.get("sql"):
                        st.code(tool["sql"], language="sql")
        st.markdown(turn["content"])

# A starter button stashes its text and reruns; the chat box handles everything else.
prompt = st.chat_input("Ask about prices, deals, or what's worth buying") or (
    st.session_state.pop("pending_text", None)
)

if prompt:
    st.session_state.chat_display.append({"role": "user", "content": prompt})
    st.session_state.chat_api.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        used: list[dict] = []
        tools_slot = st.container()

        def note_tool(name: str, payload: dict) -> None:
            used.append(
                {
                    "label": TOOL_LABEL.get(name, name),
                    "sql": payload.get("sql"),
                }
            )

        try:
            reply = st.write_stream(
                brain.stream_reply(st.session_state.chat_api, on_tool=note_tool)
            )
        except Exception as exc:  # surface API/network failures in the UI
            reply = f"Something went wrong talking to Claude: `{exc}`"
            st.error(reply)

        if used:
            with tools_slot:
                with st.expander(f"Used {len(used)} tool call(s)"):
                    for tool in used:
                        st.markdown(f"**{tool['label']}**")
                        if tool.get("sql"):
                            st.code(tool["sql"], language="sql")

    st.session_state.chat_display.append(
        {"role": "assistant", "content": reply, "tools": used}
    )
