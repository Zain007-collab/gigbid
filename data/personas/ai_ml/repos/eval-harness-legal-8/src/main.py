# eval-harness-legal-8 (synthetic)

from celery import Celery
app = Celery('tasks', broker='redis://localhost:6379/0')

from langchain_core.prompts import ChatPromptTemplate

def faithfulness(answer, contexts):  # judge-graded eval
    ...

def main() -> None:
