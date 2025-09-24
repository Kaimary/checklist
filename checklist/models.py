import json
from abc import ABC
from checklist.llm import LLM

class BaseNL2SQLModel(ABC):
    def __init__(self):
        pass

    def __call__(self, **kwargs):
        raise NotImplementedError
    
class CSCSQL7b(BaseNL2SQLModel):
    def __init__(self):
        super().__init__()
        self.model_name = "cscsql"

        dev_json = "data/bird/dev_20240627/dev.json"
        output_file = "data/bird/results/csc-sql-7b.sql"
        self.dev = json.load(open(dev_json))
        self.outputs = [line.strip() for line in open(output_file)]

    def __call__(self, **kwargs):
        nl = kwargs.get("nl", None)
        for ex in self.dev:
            if ex["question"] == nl:
                idx = self.dev.index(ex)
                return self.outputs[idx]
        raise ValueError("No matching NL found in the dev set")


class CSCSQL32b(BaseNL2SQLModel):
    def __init__(self):
        super().__init__()
        self.model_name = "cscsql"

        dev_json = "data/bird/dev_20240627/dev.json"
        output_file = "data/bird/results/csc-sql-32b.sql"
        self.dev = json.load(open(dev_json))
        self.outputs = [line.strip() for line in open(output_file)]

    def __call__(self, **kwargs):
        nl = kwargs.get("nl", None)
        for ex in self.dev:
            if ex["question"] == nl:
                idx = self.dev.index(ex)
                return self.outputs[idx]
        raise ValueError("No matching NL found in the dev set")
    
class CHESS(BaseNL2SQLModel):
    def __init__(self):
        super().__init__()
        self.model_name = "chess"

        dev_json = "data/bird/dev_20240627/dev.json"
        output_file = "data/bird/results/chess.sql"
        self.dev = json.load(open(dev_json))
        self.outputs = [line.strip() for line in open(output_file)]

    def __call__(self, **kwargs):
        nl = kwargs.get("nl", None)
        for ex in self.dev:
            if ex["question"] == nl:
                idx = self.dev.index(ex)
                return self.outputs[idx]
        raise ValueError("No matching NL found in the dev set")
    
class OMNISQL32b(BaseNL2SQLModel):
    def __init__(self):
        super().__init__()
        self.model_name = "omnisql"

        dev_json = "data/bird/dev_20240627/dev.json"
        output_file = "data/bird/results/omnisql-32b.sql"
        self.dev = json.load(open(dev_json))
        self.outputs = [line.strip() for line in open(output_file)]

    def __call__(self, **kwargs):
        nl = kwargs.get("nl", None)
        for ex in self.dev:
            if ex["question"] == nl:
                idx = self.dev.index(ex)
                return self.outputs[idx]
        raise ValueError("No matching NL found in the dev set")

class RESDSQL(BaseNL2SQLModel):
    def __init__(self):
        super().__init__()
        self.model_name = "resdsql"

        dev_json = "data/spider/dev.json"
        output_file = "data/spider/results/resdsql-3b.sql"
        self.dev = json.load(open(dev_json))
        self.outputs = [line.strip() for line in open(output_file)]

    def __call__(self, **kwargs):
        nl = kwargs.get("nl", None)
        for ex in self.dev:
            if ex["question"] == nl:
                idx = self.dev.index(ex)
                return self.outputs[idx]
        raise ValueError("No matching NL found in the dev set")

class DAILSQL(BaseNL2SQLModel):
    def __init__(self):
        super().__init__()
        self.model_name = "dailsql"

        dev_json = "data/spider/dev.json"
        output_file = "data/spider/results/dailsql.sql"
        self.dev = json.load(open(dev_json))
        self.outputs = [line.strip() for line in open(output_file)]

    def __call__(self, **kwargs):
        nl = kwargs.get("nl", None)
        for ex in self.dev:
            if ex["question"] == nl:
                idx = self.dev.index(ex)
                return self.outputs[idx]
        raise ValueError("No matching NL found in the dev set")

class GenericLLM(BaseNL2SQLModel):
    def __init__(self, model_name: str = "gpt-4o-mini-0708"):
        super().__init__()
        self.model_name = model_name

    def __call__(self, **kwargs):
        model = LLM(model_name=self.model_name)
        response = model(
            prompt=kwargs.get("prompt"),
            parser=kwargs.get("parser"),
            request_kwargs=kwargs.get("request_kwargs", {})
        )["SQL"].strip()
        return response