import os
import uuid
import random
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
from llama_index.core import Settings, SimpleDirectoryReader
from llama_index.core.postprocessor import SimilarityPostprocessor

from readFeishuWiki import readWiki, ExcelReader

from streamlit_feedback import streamlit_feedback
from langsmith.run_helpers import get_current_run_tree
from langchain_core.tracers.context import tracing_v2_enabled
from langsmith import Client, traceable

title = "AI assistant, powered by Qingcheng knowledge"
st.set_page_config(page_title=title, page_icon="🦙", layout="centered", initial_sidebar_state="auto", menu_items=None)

openai_api_base = "http://vasi.chitu.ai/v1"
os.environ["OPENAI_API_BASE"] = openai_api_base
os.environ["OPENAI_API_KEY"] = st.secrets.openai_key
os.environ["LANGCHAIN_PROJECT"] = "July"
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

prompt = "You are an expert ai infra analyst at 清程极智. Use your knowledge base to answer questions about ai model/hardware performance. Show URLs of your sources whenever possible"
openai.api_key = st.secrets.openai_key

os.environ["ANTHROPIC_API_KEY"] = st.secrets.claude_key
os.environ["JINAAI_API_KEY"] = st.secrets.jinaai_key

st.title(title)
    
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
        embed_model = OpenAIEmbedding(model="text-embedding-3-large", api_base=openai_api_base)
        # from llama_index.core import VectorStoreIndex
        # index = VectorStoreIndex.from_documents([], embed_model=embed_model)
        index, fileToTitleAndUrl = asyncio.run(readWiki(space_id, app_id, app_secret, embed_model))
        
        return index, fileToTitleAndUrl 
   

llm_map = {"Claude3.5": Anthropic(model="claude-3-5-sonnet-20240620", system_prompt=prompt), 
           "gpt4o": OpenAI(model="gpt-4o", system_prompt=prompt),
           "gpt3.5": OpenAI(model="gpt-3.5-turbo", temperature=0.5, system_prompt=prompt),
           "Llama3_8B": OpenAI(base_url="http://localhost:2512/v1", system_prompt=prompt),
           "ollama": Ollama(model="llama2", request_timeout=60.0)
}

def toggle_llm():
    llm = st.sidebar.selectbox(
        "模型切换",
        ("gpt4o", "Claude3.5", "Llama3_8B"),
        index=1
    )
    if llm=="Llama3_8B":
        os.environ["OPENAI_API_KEY"] = "aa"
    else:
        os.environ["OPENAI_API_KEY"] = st.secrets.openai_key

    if llm!=st.session_state["llm"]:
        st.session_state["llm"] = llm
        Settings.llm = llm_map[llm]
        st.rerun()


def toggle_rag_use():
    
    from llama_index.core import VectorStoreIndex
    from llama_index.core import Document
    placeholder_doc = Document(text="blah")
    index = VectorStoreIndex.from_documents([placeholder_doc])
        
    use_rag = st.sidebar.selectbox(
        "是否用知识库",
        ("是", "否")
    )
    use_rag = True if use_rag=="是" else False
    
    uploaded_files = st.sidebar.file_uploader(label="上传临时文件", accept_multiple_files=True)
    if uploaded_files:
        use_rag = False
        from upsertS3 import upload_file, create_bucket, create_presigned_url

        directory = st.session_state.session_id
        os.makedirs(directory)
        if st.secrets.aws_region=='us-east-1':
            region = None
        else:
            region = st.secrets.aws_region
        bucket_created = create_bucket(bucket_name=directory, region=region)
        if bucket_created:
            for file in uploaded_files:
                filename = file.name
                upload_file(filename, bucket=directory)
                bytes_data = file.read()
                with open(filename, 'w') as f:
                    f.write(bytes_data)
                
                s3_url = create_presigned_url(bucket_name=directory, object_name=filename)
                st.session_state.fileToTitleAndUrl[filename] = {"url": s3_url}
                
            reader = SimpleDirectoryReader(
                        input_dir=directory, 
                        recursive=True, 
                        file_extractor={".xlsx": ExcelReader()}, 
                        file_metadata=lambda filename: {"file_name": st.session_state.fileToTitleAndUrl.get(filename, {}).get("url")}
                    )
            docs = reader.load_data()
            index = VectorStoreIndex.from_documents(docs)
        
    if use_rag!= st.session_state.use_rag:
        if use_rag:
            index, st.session_state.fileToTitleAndUrl = load_data()                
        
        st.session_state.chat_engine = index.as_chat_engine(chat_mode="condense_question", streaming=True)
        st.session_state.use_rag = use_rag
        st.rerun()
    else:
        if "chat_engine" not in st.session_state.keys():
            index, st.session_state.fileToTitleAndUrl = load_data()
            st.session_state.chat_engine = index.as_chat_engine(chat_mode="condense_question", streaming=True)

def init_chat():
             
    if "llm" not in st.session_state.keys(): 
        st.session_state.llm = "claude3.5"
    if "use_rag" not in st.session_state.keys(): 
        st.session_state.use_rag = True
    if "fileToTitleAndUrl" not in st.session_state.keys(): 
        st.session_state.fileToTitleAndUrl = {}
    
    toggle_llm()
    toggle_rag_use()
    
    if "messages" not in st.session_state.keys() or len(st.session_state.messages)==0 or st.sidebar.button("清空对话"): # Initialize the chat messages history
        st.session_state.session_id = None
        st.session_state.run_id = None
        st.session_state.chat_engine.reset()
        st.session_state.messages = []
    
def starter_prompts():
    prompt = ""
    demo_prompts = ["应该如何衡量decode和prefill过程的性能?", "AI Infra行业发展的目标是什么?", "JSX有什么优势?", "怎么实现capcha/防ai滑块?", "官网首页展示有哪些前端方案?", "我们的前端开发面试考察些什么?", "介绍一下CHT830项目", "llama模型平均吞吐量(token/s)多少?"]
    selected_prompts = random.sample(demo_prompts, 4)

    st.markdown("""<style>
    .stButton button {
        display: inline-block;
        width: 100%;
        height: 80px;
    }
    </style>""", unsafe_allow_html=True)
                
    cols = st.columns(4, vertical_alignment="center")
    for i, demo_prompt in enumerate(selected_prompts):
        with cols[i]:
            if st.button(demo_prompt):
                prompt = demo_prompt
                break

    return prompt

@traceable(name=st.session_state.session_id)
def main():
    run = get_current_run_tree()
    run_id = str(run.id)
    st.session_state.run_id = st.session_state["run_0"] = run_id
    
    feedback_option = "faces" if st.toggle(label="`Thumbs` ⇄ `Faces`", value=False) else "thumbs"

    feedback_kwargs = {
        "feedback_type": feedback_option,
        "optional_text_label": "Please provide extra information",
    }
    
    init_chat()

    prompt = ""
    if len(st.session_state.messages)==0 and st.session_state.use_rag:
        prompt = starter_prompts() 
        # col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
        # with col4:
        #     if st.button("试试别的问题"):
        #         selected_prompts = random.sample(demo_prompts, 4)

    if not prompt:
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
                try:
                    if prompt:
                        streaming_response = st.session_state.chat_engine.stream_chat(prompt)
                    else:
                        st.rerun()
                except:
                    st.rerun()
                for token in streaming_response.response_gen:
                    response_msg += token
                    response_container.write(response_msg)
                
                if st.session_state.use_rag:
                    processor = SimilarityPostprocessor(similarity_cutoff=0.25)
                    source_nodes = streaming_response.source_nodes
                    filtered_nodes = processor.postprocess_nodes(source_nodes)
                    sources_list = []
                    for node in filtered_nodes:
                        try:
                            file_path = node.metadata["file_path"]
                            file_name = st.session_state.fileToTitleAndUrl[file_path]["title"]
                            file_url = st.session_state.fileToTitleAndUrl[file_path]["url"]
                            source = "[%s](%s)中某部分相似度" % (file_name, file_url) + format(node.score, ".2%") 
                            sources_list.append(source)
                        except Exception as e:
                            # no source wiki node
                            print(e)
                            pass
                    
                    if sources_list: 
                        sources = "  \n".join(sources_list)
                        source_msg = "  \n  \n***知识库引用***  \n" + sources
                        
                        for c in source_msg:
                            response_msg += c
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
            
            # clear starter prompts upon convo
            if len(st.session_state.messages)==2:
                st.rerun()
        
main()
