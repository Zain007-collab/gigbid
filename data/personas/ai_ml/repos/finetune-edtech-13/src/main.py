# finetune-edtech-13 (synthetic)

from transformers import AutoModelForSequenceClassification

def faithfulness(answer, contexts):  # judge-graded eval
    ...

import mlflow
mlflow.log_metric('f1', f1)

import torch
from torch import nn
