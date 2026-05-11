#可用模型列表，以及获得访问模型的客户端
#实际使用时可以根据自己的实际情况调整
ALI_TONGYI_API_KEY_OS_VAR_NAME = "DASHSCOPE_API_KEY"
ALI_TONGYI_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ALI_TONGYI_MAX_MODEL = "qwen-max-2025-01-25"
ALI_TONGYI_PLUS_MODEL = "qwen-plus-2025-09-11"
ALI_TONGYI_TURBO_MODEL = "qwen-turbo-0919"
ALI_TONGYI_DEEPSEEK_R1 = "deepseek-r1"
ALI_TONGYI_DEEPSEEK_V3 = "deepseek-v3"
ALI_TONGYI_EMBEDDING_MODEL = "text-embedding-v3"
ALI_TONGYI_REANK_MODEL = "dashscope-rerank-v1"
DEEPSEEK_API_KEY_OS_VAR_NAME = "Deepseek_Key"
DEEPSEEK_URL = "https://api.deepseek.com/v1"
DEEPSEEK_CHAT_MODEL = "deepseek-chat"
DEEPSEEK_REASONER_MODEL = "deepseek-reasoner"

import os
from langchain_openai import ChatOpenAI
from openai import OpenAI
import inspect
from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank

def get_lc_o_model_client(api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME),
                          base_url=ALI_TONGYI_URL,
                          model=ALI_TONGYI_MAX_MODEL, temperature = 0.7, verbose=False, debug=False):
    '''
    以OpenAI兼容的方式，通过LangChain获得指定平台和模型的客户端
    可以通过传入api_key，base_url，model，temperature四个参数来覆盖默认值
    verbose，debug两个参数，分别控制是否输出调试信息，是否输出详细调试信息，默认不打印
    :return: 指定平台和模型的客户端，默认平台和模型为阿里百炼qwen-max-latest，温度=0.7
    '''
    function_name = inspect.currentframe().f_code.co_name
    if(verbose):
        print(f"{function_name}-平台：{base_url},模型：{model},温度：{temperature}")
    if(debug):
        print(f"{function_name}-平台：{base_url},模型：{model},温度：{temperature},key：{api_key}")
    return ChatOpenAI(api_key=api_key, base_url=base_url,model=model,temperature=temperature)

def get_lc_o_ali_model_client(model=ALI_TONGYI_MAX_MODEL, temperature = 0.7, verbose=False, debug=False):
    '''
    以OpenAI兼容的方式，通过LangChain获得阿里大模型的客户端
    可以通过传入model，temperature 两个参数来覆盖默认值
    verbose，debug两个参数，分别控制是否输出调试信息，是否输出详细调试信息，默认不打印
    :return: 指定平台和模型的客户端，默认模型为阿里百炼里的qwen-plus，温度=0.7
    '''
    return get_lc_o_model_client(api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME), base_url=ALI_TONGYI_URL
                                 , model=model, temperature =temperature, verbose=verbose, debug=debug)

def get_lc_o_ds_model_client(model=DEEPSEEK_CHAT_MODEL, temperature = 0.7, verbose=False, debug=False):
    '''
    以OpenAI兼容的方式，通过LangChain获得DeepSeek大模型的客户端
    可以通过传入model，temperature 两个参数来覆盖默认值
    verbose，debug两个参数，分别控制是否输出调试信息，是否输出详细调试信息，默认不打印
    :return: 指定平台和模型的客户端，默认模型为DeepSeek的deepseek-chat，温度=0.7
    '''
    return get_lc_o_model_client(api_key=os.getenv(DEEPSEEK_API_KEY_OS_VAR_NAME), base_url=DEEPSEEK_URL
                                 , model=model, temperature =temperature, verbose=verbose, debug=debug)

def get_normal_client(api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME), base_url=ALI_TONGYI_URL,
                      verbose=False, debug=False):
    """
    使用原生api获得指定平台的客户端，但未指定具体模型，缺省平台为阿里云百炼
    也可以通过传入api_key，base_url两个参数来覆盖默认值
    verbose，debug两个参数，分别控制是否输出调试信息，是否输出详细调试信息，默认不打印
    """
    function_name = inspect.currentframe().f_code.co_name
    if (verbose):
        print(f"{function_name}-平台：{base_url}")
    if (debug):
        print(f"{function_name}-平台：{base_url},key：{api_key}")
    return OpenAI(api_key=api_key, base_url=base_url)

def get_ali_model_client(verbose=False, debug=False):
    """
    获取阿里云百炼的原生客户端
    """
    return get_normal_client(
        api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME),
        base_url=ALI_TONGYI_URL,
        verbose=verbose,
        debug=debug
    )

def get_ali_embeddings(model=ALI_TONGYI_EMBEDDING_MODEL, verbose=False):
    """
    获取阿里云的 Embedding 模型
    使用 DashScope 原生 API
    """
    from langchain_community.embeddings import DashScopeEmbeddings
    if verbose:
        print(f"使用阿里云 Embedding 模型: {model}")
    return DashScopeEmbeddings(
        model=model,
        dashscope_api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME)
    )

def get_openai_embeddings(model="text-embedding-3-small", verbose=False):
    """
    获取 OpenAI 的 Embedding 模型（备用方案）
    """
    from langchain_openai import OpenAIEmbeddings
    if verbose:
        print(f"使用 OpenAI Embedding 模型: {model}")
    return OpenAIEmbeddings(
        model=model,
        openai_api_key=os.getenv("OPENAI_API_KEY")
    )



def get_ali_rerank(top_n=3):
    '''
    通过LangChain获得一个阿里重排序模型的实例
    基于阿里云的 DashScope 大模型 实现，通过语义理解能力对文档与查询的匹配度进行二次评估。
    相比传统检索，语义重排序会增加一定延迟，适合对精度要求高的场景。
    :return: 阿里通义千问嵌入模型的实例
    '''
    return DashScopeRerank(
        model=ALI_TONGYI_REANK_MODEL, dashscope_api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME),
        top_n=top_n
)
