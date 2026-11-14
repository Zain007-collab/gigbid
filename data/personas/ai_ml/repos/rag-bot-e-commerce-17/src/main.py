# rag-bot-e-commerce-17 (synthetic)

from fastapi import FastAPI
app = FastAPI()

from langchain_core.prompts import ChatPromptTemplate

def faithfulness(answer, contexts):  # judge-graded eval
    ...

from pinecone import Pinecone
index = Pinecone(api_key=KEY).Index('docs')
