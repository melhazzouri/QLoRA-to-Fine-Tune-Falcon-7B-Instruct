# -*- coding: utf-8 -*-
"""Falcon-7B-Instruct QLoRA.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1FxlUb_H6Xirhkx4RszAgHeb2uDW7oKIH

# Install
"""

import locale
def getpreferredencoding(do_setlocale = True):
    return "UTF-8"
locale.getpreferredencoding = getpreferredencoding

!pip install -q -U bitsandbytes
!pip install -q -U git+https://github.com/huggingface/transformers.git
!pip install -q -U git+https://github.com/huggingface/peft.git
!pip install -q -U git+https://github.com/huggingface/accelerate.git
!pip install -q -U einops
!pip install -q -U safetensors
!pip install -q -U torch
!pip install -q -U xformers
!pip install -q -U datasets

"""# Import"""

from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig, pipeline
import transformers
import torch
from torch.utils.data import DataLoader, Dataset
import torch
from transformers import AutoTokenizer

"""# Load Quantized Model"""

model_id = "vilsonrodrigues/falcon-7b-instruct-sharded" # sharded model by vilsonrodrigues
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_use_double_quant=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16
)

tokenizer = AutoTokenizer.from_pretrained(model_id)
tokenizer.pad_token = tokenizer.eos_token
model = AutoModelForCausalLM.from_pretrained(model_id, quantization_config=bnb_config, device_map={"":0}, trust_remote_code=True)

from peft import prepare_model_for_kbit_training

model.gradient_checkpointing_enable()
model = prepare_model_for_kbit_training(model)

def print_trainable_parameters(model):
    """
    Prints the number of trainable parameters in the model.
    """
    trainable_params = 0
    all_param = 0
    for _, param in model.named_parameters():
        all_param += param.numel()
        if param.requires_grad:
            trainable_params += param.numel()
    print(
        f"trainable params: {trainable_params} || all params: {all_param} || trainable%: {100 * trainable_params / all_param}"
    )

from peft import LoraConfig, get_peft_model

config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["query_key_value"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)

model = get_peft_model(model, config)
print_trainable_parameters(model)

"""# Prepare Data"""

from datasets import load_dataset

data = load_dataset("truthful_qa", "generation")

tokenizer.pad_token = tokenizer.eos_token

# prompt_template = "### Instruction: {prompt}\n### Response:"

train_dataset = data['validation'].select(range(100)).map(lambda x: {"input_text": x['question']  + "\n" + x['best_answer']})

# Tokenize the datasets
train_encodings = tokenizer(train_dataset['input_text'], truncation=True, padding=True, max_length=256, return_tensors='pt')

class TextDataset(Dataset):
    def __init__(self, encodings):
        self.encodings = encodings

    def __getitem__(self, idx):
        item = {key: torch.tensor(val[idx]) for key, val in self.encodings.items()}
        item["labels"] = item["input_ids"].clone()
        return item

    def __len__(self):
        return len(self.encodings["input_ids"])

# Convert the encodings to PyTorch datasets
train_dataset = TextDataset(train_encodings)

"""# Example Before Fine Tuning"""

def generate(index):

  example_text = data['validation'][index]['question']
  correct_answer = data['validation'][index]['best_answer']

  print("Question:")
  print(example_text)

  encoding = tokenizer(example_text, return_tensors="pt").to("cuda:0")
  output = model.generate(input_ids=encoding.input_ids, attention_mask=encoding.attention_mask, max_new_tokens=100, do_sample=True, temperature=0.000001, eos_token_id=tokenizer.eos_token_id, top_k = 0)

  print("Answer:")
  print(tokenizer.decode(output[0], skip_special_tokens=True))

  print("Best Answer:")
  print(correct_answer)

  print()

generate(0)

"""# Training"""

trainer = transformers.Trainer(
    model=model,
    train_dataset=train_dataset,
    # eval_dataset=val_dataset,
    args=transformers.TrainingArguments(
        num_train_epochs=30,
        per_device_train_batch_size=16,
        gradient_accumulation_steps=4,
        warmup_ratio=0.05,
        # max_steps=100,
        learning_rate=2e-4,
        fp16=True,
        logging_steps=1,
        output_dir="outputs",
        optim="paged_adamw_8bit",
        lr_scheduler_type='cosine',
    ),
    data_collator=transformers.DataCollatorForLanguageModeling(tokenizer, mlm=False),
)
model.config.use_cache = False  # silence the warnings. Please re-enable for inference!
trainer.train()

"""# Example After Fine Tuning"""

model.config.use_cache = True
model.eval()

generate(0)

"""# Merge Adapters and Save Model to Hub"""

from huggingface_hub import notebook_login

notebook_login()

model.save_pretrained("falcon-7b-instruct-ft-adapters", push_to_hub=True) # save LORA adapters
model.push_to_hub("falcon-7b-instruct-ft-adapters")

from transformers import AutoModelForCausalLM, PretrainedConfig
import torch

# reload the base model (you might need pro subscription for this)
model = AutoModelForCausalLM.from_pretrained("tiiuae/falcon-7b-instruct", device_map={"":0}, trust_remote_code=True, torch_dtype=torch.float16)

from peft import PeftModel

# load perf model with new adapters
model = PeftModel.from_pretrained(
    model,
    "vrsen/falcon-7b-instruct-ft-adapters",
)

model = model.merge_and_unload() # merge adapters with

model.push_to_hub("falcon-7b-instruct-ft")

from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("tiiuae/falcon-7b-instruct", trust_remote_code=True)
tokenizer.push_to_hub("falcon-7b-instruct-ft", use_auth_token=True)