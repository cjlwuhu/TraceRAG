
import base64
import requests
import streamlit as st


root = "http://localhost:8000/v1"


def post_request(data, endpoint):
    url = f"{root}/{endpoint}"
    response = requests.post(url, json=data, timeout=300)
    response.raise_for_status()
    return response.json()


st.set_page_config(page_title="EasyRAG 多模态智能助手", layout="wide")

st.markdown(
    "## EasyRAG 多模态智能助手\n"
    "1. 输入问题。\n"
    "2. 可选上传图片，系统会先将图片解析为 Markdown 文本。\n"
    "3. 系统将“问题 + 图片描述”送入 RAG 检索流程。\n"
    "4. 等待智能助手返回答案。"
)

with st.form(key="qa_form"):
    query = st.text_area("输入问题:", height=120)

    uploaded_image = st.file_uploader(
        "上传图片（可选，支持 png / jpg / jpeg / webp）:",
        type=["png", "jpg", "jpeg", "webp"]
    )

    if uploaded_image is not None:
        st.image(
            uploaded_image,
            caption="已上传图片预览",
            width=300
        )

    document = st.selectbox(
        "选择文档来源（可选）:",
        options=list(["无", "director", "emsplus", "rcp", "umac"])
    )

    submit = st.form_submit_button("开始回答")


if submit:
    if not query.strip() and uploaded_image is None:
        st.warning("请至少输入一个问题，或者上传一张图片。")
    else:
        with st.spinner("生成中，请稍候..."):
            data = {
                "query": query,
            }

            if document is not None and document != "无":
                data["document"] = document

            if uploaded_image is not None:
                image_bytes = uploaded_image.getvalue()
                image_base64 = base64.b64encode(image_bytes).decode("utf-8")

                data["image_base64"] = image_base64
                data["image_mime"] = uploaded_image.type or "image/png"

            try:
                res = post_request(data, endpoint="rag")
            except Exception as e:
                res = {
                    "answer": f"请求后端失败：{str(e)}",
                    "contexts": [],
                    "image_markdown": "",
                }

        st.markdown("## 答案")
        st.markdown(res.get("answer", ""))

        image_markdown = res.get("image_markdown", "")
        if image_markdown:
            st.markdown("## 图片解析结果")
            st.markdown(image_markdown)

        contexts = res.get("contexts", [])
        st.markdown("## 文档列表")
        if not contexts:
            st.info("没有返回文档上下文。")
        else:
            for i, context in enumerate(contexts):
                with st.expander(f"文档 {i}"):
                    st.markdown(f"{context}")
