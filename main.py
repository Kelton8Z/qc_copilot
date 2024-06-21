import os
import uuid
import asyncio
import requests
import lark_oapi as lark
from lark_oapi.api.wiki.v2 import *
from lark_oapi.api.docx.v1 import *
from lark_oapi.api.auth.v3 import *
import streamlit as st
import openai
from functools import partial
from llama_index.llms.ollama import Ollama
from llama_index.llms.openai import OpenAI
from llama_index.llms.anthropic import Anthropic
from llama_index.core import Settings

from readFeishuWiki import readWiki

from streamlit_feedback import streamlit_feedback
from langsmith.run_helpers import get_current_run_tree
from langchain_core.tracers.context import tracing_v2_enabled
from langsmith import Client, traceable

title = "AI assistant, powered by Qingcheng knowledge"
st.set_page_config(page_title=title, page_icon="🦙", layout="centered", initial_sidebar_state="auto", menu_items=None)

os.environ["OPENAI_API_BASE"] = "https://vasi.chitu.ai/v1"
os.environ["OPENAI_API_KEY"] = st.secrets.openai_key
os.environ["LANGCHAIN_PROJECT"] = "stage"
os.environ["LANGCHAIN_TRACING_V2"] = "true" 
os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
langchain_api_key = os.environ["LANGCHAIN_API_KEY"] = st.secrets.langsmith_key

langsmith_project_id = st.secrets.langsmith_project_id
langsmith_client = Client(api_key=langchain_api_key)

app_id = st.secrets.feishu_app_id
app_secret = st.secrets.feishu_app_secret
space_id = st.secrets.feishu_space_id
client = lark.Client.builder() \
        .enable_set_token(True) \
        .log_level(lark.LogLevel.DEBUG) \
        .app_id(app_id) \
        .app_secret(app_secret) \
        .build()
        

# Initialize session state
if 'session_id' not in st.session_state or not st.session_state.session_id:
    st.session_state['session_id'] = str(uuid.uuid4())

prompt = "You are an expert AI engineer in our company Qingcheng and your job is to answer technical questions. Keep your answers technical and based on facts – do not hallucinate features."
openai.api_key = st.secrets.openai_key

os.environ["ANTHROPIC_API_KEY"] = st.secrets.claude_key
os.environ["JINAAI_API_KEY"] = st.secrets.jinaai_key

llm_map = {"claude": Anthropic(model="claude-3-opus-20240229"), 
           "gpt4o": OpenAI(model="gpt-4o", system_prompt=prompt),
           "gpt3.5": OpenAI(model="gpt-3.5-turbo", temperature=0.5, system_prompt=prompt),
           "ollama": Ollama(model="llama2", request_timeout=60.0)
}

st.title(title)
         
if "messages" not in st.session_state.keys(): # Initialize the chat messages history
    st.session_state.messages = [
        {"role": "assistant", "content": "Ask me a question!"}
    ]

    
def _submit_feedback(user_response, emoji=None, run_id=None):
    feedback = user_response['score']
    feedback_text = user_response['text']
    # st.toast(f"Feedback submitted: {feedback}", icon=emoji)
    messages = st.session_state.messages
    if len(messages)>1:
        langsmith_client.create_feedback(
            run_id,
            key="user-score",
            score=0.0 if feedback=="👎" else 1.0,
            comment=f'{messages[-2]["content"]} + {messages[-1]["content"]} -> ' + feedback_text if feedback_text else "",
        )
    return user_response

@st.cache_resource(show_spinner=False)
def load_data():
    with st.spinner(text="Loading and indexing the docs – hang tight! This should take 1-2 minutes."):
        app_id = st.secrets.feishu_app_id
        app_secret = st.secrets.feishu_app_secret
        # recursively read wiki and write each file into the machine
        # from llama_index.embeddings.jinaai import JinaEmbedding
        # embed_model = JinaEmbedding(
        #     api_key=st.secrets.jinaai_key,
        #     model="jina-embeddings-v2-base-en",
        #     embed_batch_size=16,
        # )
        from llama_index.embeddings.openai import OpenAIEmbedding
        embed_model = OpenAIEmbedding(model="text-embedding-3-large")
        # from llama_index.core import VectorStoreIndex
        # index = VectorStoreIndex.from_documents([], embed_model=embed_model)
        index = asyncio.run(readWiki(space_id, app_id, app_secret, embed_model))
        
        return index
    
@traceable(project_name="null")
def main():
    run = get_current_run_tree()
    run_id = str(run.id)
    st.session_state.run_id = st.session_state["run_0"] = run_id

    Settings.llm = llm_map["gpt4o"]
    index = load_data()

    if "chat_engine" not in st.session_state.keys(): # Initialize the chat engine
        st.session_state.chat_engine = index.as_chat_engine(chat_mode="condense_question", streaming=True)
        
    if st.sidebar.button("Clear message history"):
        print("Clearing message history")
        st.session_state.session_id = None
        st.session_state.run_id = None
        st.session_state.messages = []
        st.session_state.chat_engine.reset()
        
    
    feedback_option = "faces" if st.toggle(label="`Thumbs` ⇄ `Faces`", value=False) else "thumbs"

    feedback_kwargs = {
        "feedback_type": feedback_option,
        "optional_text_label": "Please provide extra information",
    }
    
    # Prompt for user input and save to chat history
    prompt = st.chat_input("Your question")
    if prompt: 
        st.session_state.messages.append({"role": "user", "content": prompt})        

    # Display the prior chat messages
    for i, message in enumerate(st.session_state.messages): 
        with st.chat_message(message["role"]):
            st.write(message["content"])
            
    
        if message["role"]=="assistant":
            feedback_key = f"feedback_{int(i/2)}"
            # This actually commits the feedback
            streamlit_feedback(
                **feedback_kwargs,
                key=feedback_key,
                on_submit=partial(
                    _submit_feedback, run_id=st.session_state[f"run_{int(i/2)}"]
                ),
            )

    if st.session_state.messages:
        message = st.session_state.messages[-1]
        # If last message is not from assistant, generate a new response
        if message["role"] != "assistant":
            with st.chat_message("assistant"):
                response_container = st.empty()  # Container to hold the response as it streams
                response_msg = ""
                streaming_response = st.session_state.chat_engine.stream_chat(prompt)
                for token in streaming_response.response_gen:
                    response_msg += token
                    response_container.write(response_msg)

                message = {"role": "assistant", "content": response_msg}
                st.session_state.messages.append(message) # Add response to message history
                
                # log nonnull converstaion to langsmith
                if prompt and response_msg:
                    print(f'{prompt} -> {response_msg}')
                    requests.patch(
                        f"https://api.smith.langchain.com/runs/{run_id}",
                        json={
                            "name": st.session_state.session_id,
                            "inputs": {"text": prompt},
                            "outputs": {"my_output": response_msg},
                        },
                        headers={"x-api-key": langchain_api_key},
                    )
                    
                # st.rerun()
                with tracing_v2_enabled(os.environ["LANGCHAIN_PROJECT"]) as cb:
                    feedback_index = int(
                        (len(st.session_state.messages) - 1) / 2
                    )
                    st.session_state[f"run_{feedback_index}"] = run.id
                    run = cb.latest_run
                    streamlit_feedback(**feedback_kwargs, key=f"feedback_{feedback_index}")

main()
