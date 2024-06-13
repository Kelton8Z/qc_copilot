import os 
import requests
import json
import lark_oapi as lark
from lark_oapi.api.wiki.v2 import *
from lark_oapi.api.docx.v1 import *
from lark_oapi.api.auth.v3 import *
import streamlit as st
from listAllWiki import *

from llama_index.embeddings.jinaai import JinaEmbedding
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
from llama_index.core.readers.base import BaseReader
import pandas as pd

class ExcelReader(BaseReader):
    def load_data(self, file_path: str, extra_info: dict = None):
        data = pd.read_excel(file_path).to_string()
        return [Document(text=data, metadata=extra_info)]

app_id = st.secrets.feishu_app_id
app_secret = st.secrets.feishu_app_secret

client = lark.Client.builder() \
        .enable_set_token(True) \
        .log_level(lark.LogLevel.DEBUG) \
        .app_id(app_id) \
        .app_secret(app_secret) \
        .build()

def getAppAccessToken(app_id, app_secret):
    # 构造请求对象
    request: InternalAppAccessTokenRequest = InternalAppAccessTokenRequest.builder() \
        .request_body(InternalAppAccessTokenRequestBody.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()) \
        .build()

    # 发起请求
    response: InternalAppAccessTokenResponse = client.auth.v3.app_access_token.internal(request)

    # 处理失败返回
    if not response.success():
        lark.logger.error(
            f"client.auth.v3.app_access_token.internal failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}")
        return

    # 处理业务结果
    lark.logger.info(lark.JSON.marshal(response.data, indent=4))

    return response.data["app_access_token"]

def getOAuthCode(app_id, redirect_url):
    url = f"https://open.feishu.cn/open-apis/authen/v1/authorize?app_id={app_id}&redirect_uri={redirect_url}"
    headers = {
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers)
    if response.status_code == 200:
        return response.data["code"]
    else:
        print("Failed to get oauth code. Status code:", response.status_code)

def getTenantAccessToken(app_id, app_secret):
    request: InternalTenantAccessTokenRequest = InternalTenantAccessTokenRequest.builder() \
        .request_body(InternalTenantAccessTokenRequestBody.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()) \
        .build()

    # 发起请求
    response: InternalTenantAccessTokenResponse = client.auth.v3.tenant_access_token.internal(request)

    # 处理失败返回
    if not response.success():
        lark.logger.error(
            f"client.auth.v3.tenant_access_token.internal failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}")
        return

    # 处理业务结果
    lark.logger.info(lark.JSON.marshal(response, indent=4))
    return json.loads(response.raw.content)["tenant_access_token"]

def getUserAccessToken(oauth_code):
    # 构造请求对象
    request: CreateOidcAccessTokenRequest = CreateOidcAccessTokenRequest.builder() \
        .request_body(CreateOidcAccessTokenRequestBody.builder()
            .grant_type("authorization_code")
            .code(oauth_code)
            .build()) \
        .build()

    # 发起请求
    response: CreateOidcAccessTokenResponse = client.authen.v1.oidc_access_token.create(request)

    # 处理失败返回
    if not response.success():
        lark.logger.error(
            f"client.authen.v1.oidc_access_token.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}")
        return

    # 处理业务结果
    lark.logger.info(lark.JSON.marshal(response.data, indent=4))

    return response.data["access_token"]

# get oauth code to get user access token
redirect_url = "https://open.feishu.cn/api-explorer/cli_a6df1d71d5f2d00d"
# app_access_token = getAppAccessToken(app_id, app_secret)
# oauth_code = getOAuthCode(app_id, redirect_url)
# user_access_token = getUserAccessToken(oauth_code)

async def readWiki(space_id, app_id, app_secret):
    tenant_access_token = getTenantAccessToken(app_id, app_secret)
    nodes = await get_all_wiki_nodes(space_id, tenant_access_token)
    print(nodes)
    for node in nodes:
        doc_id = node["obj_token"]
        title = node["title"]
        doc_type = node["obj_type"]
        
        # 构造请求对象
        # request: GetDocumentRequest = GetDocumentRequest.builder() \
        #     .document_id(doc_id) \
        #     .build()

        # 发起请求
        option = lark.RequestOption.builder().tenant_access_token(tenant_access_token).build()
        # response: GetDocumentResponse = client.docx.v1.document.get(request, option)

        # # 处理失败返回
        # if not response.success():
        #     lark.logger.error(
        #         f"client.docx.v1.document.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}")
        #     return

        # # 处理业务结果
        # lark.logger.info(lark.JSON.marshal(response.data, indent=4))

        # title = response.data.document.title
        # doc_id = response.data.document.id

        

        if doc_type=="sheet":
            pass
        elif doc_type=="docx":
            request: RawContentDocumentRequest = RawContentDocumentRequest.builder() \
            .document_id(doc_id) \
            .lang(0) \
            .build()

            # 发起请求
            response: RawContentDocumentResponse = client.docx.v1.document.raw_content(request, option)
            if not response.success():
                lark.logger.error(
                    f"client.docx.v1.document.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, doc_id: {doc_id}")
            else:
                with open("./data/"+title, 'w') as f:
                    f.write(response.data.content)

            request: ListDocumentBlockRequest = ListDocumentBlockRequest.builder() \
            .document_id(doc_id) \
            .page_size(500) \
            .document_revision_id(-1) \
            .build()

            # 发起请求
            listBlockResponse: ListDocumentBlockResponse = client.docx.v1.document_block.list(request)

            # 处理失败返回
            if not listBlockResponse.success():
                lark.logger.error(
                    f"client.docx.v1.document_block.list failed, code: {listBlockResponse.code}, msg: {listBlockResponse.msg}, log_id: {listBlockResponse.get_log_id()}")
            else:
                # 处理业务结果
                lark.logger.info(lark.JSON.marshal(listBlockResponse.data, indent=4))
        
    directory = "./data"
    if not os.path.exists(directory):
        os.makedirs(directory)
    
    reader = SimpleDirectoryReader(input_dir=directory, recursive=True, file_extractor={".xlsx": ExcelReader()})
    docs = reader.load_data()
    

    embed_model = JinaEmbedding(
        api_key=st.secrets.jinaai_key,
        model="jina-embeddings-v2-base-en",
        embed_batch_size=16,
    )
    index = VectorStoreIndex.from_documents(docs, embed_model=embed_model)
    return index

    # # 处理失败返回
    # if not response.success():
    #     lark.logger.error(
    #         f"client.wiki.v2.space_node.list failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}")
    #     return

    # # 处理业务结果
    # lark.logger.info(lark.JSON.marshal(response.data, indent=4))

def searchWiki(space_id, node_id, query, user_access_token):

    # Define the URL and the headers
    url = "https://open.feishu.cn/open-apis/wiki/v1/nodes/search"
    headers = {
        "Content-Type": "application/json",
        "Authorization": user_access_token
    }

    # Define the request body parameters
    data = {
        "space_id": space_id,
        "node_id": node_id,
        "query": query
    }

    # Make the POST request
    response = requests.post(url, headers=headers, data=json.dumps(data))

    # Check the response
    if response.status_code == 200:
        result = response.json()
        print("Search Results:", result)
    else:
        print("Failed to search nodes. Status code:", response.status_code)

# readWiki(space_id)
# query = "Case 1"
# searchWiki(space_id, node_id, query, user_access_token)