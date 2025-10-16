import os
from pathlib import Path
import re
import json
import hashlib
import logging
from dotenv import load_dotenv
import numpy as np
from munch import Munch
from abc import ABC, abstractmethod
from checklist.llm import LLM
from checklist.database_manager import DatabaseManager
from checklist.database_utils.db_catalog.csv_utils import load_tables_description

load_dotenv(override=True)
TEST_INSTANCE_ROOT_PATH = Path(os.getenv("TEST_INSTANCE_ROOT_PATH"))

def hashing(**kwargs):
    combined = ""
    nl = kwargs.get("nl", None)
    sql = kwargs.get("sql", None)
    if nl is not None: combined += f"{nl}"
    if sql is not None:
        normalized_sql = re.sub(r"\s+", " ", sql.strip().lower())
        combined += f";{normalized_sql}" if combined else f"{normalized_sql}"
    hashing_str = hashlib.md5(combined.encode()).hexdigest()[:8]
    
    return hashing_str

class ValidationError(Exception):
    pass

class SchemaCache:
    _cache = {}

    @classmethod
    def get_schema(cls, db_id, db_path, db_root_path):
        if db_id not in cls._cache:
            schema = DatabaseManager(db_id=db_id, db_root_path=db_root_path).get_db_schema()
            # schema_with_examples = load_schema_with_examples(_get_unique_values(db_path))
            schema_with_descriptions = load_tables_description(db_path, use_value_description=True)
            cls._cache[db_id] = DatabaseManager().get_database_schema_string(
                tentative_schema=schema,
                schema_with_examples=None, #schema_with_examples,
                schema_with_descriptions=schema_with_descriptions,
                include_value_description=True
            )
        return cls._cache[db_id]

class TestClass(ABC):
    def __init__(self, name, abbrev_name, abbrev_type, nl, hint, sql, db_id, db_root_path, key="nl+sql",
                 backbone_llm_model_name="gpt-4o-mini-0708", num=1, criteria=1.0, use_cache=False, debug=None):
        self.name = name

        self.nl=nl
        self.hint=hint
        self.sql=sql

        self.db_id=db_id
        self.db_root_path=db_root_path
        self.db_path = os.path.join(self.db_root_path, self.db_id, f"{self.db_id}.sqlite")
        self.schema_string = SchemaCache.get_schema(db_id, self.db_path, db_root_path)
        
        kwargs = {"nl": self.nl if "nl" in key else None, "sql": self.sql if "sql" in key else None}
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, abbrev_type, abbrev_name, self.db_id, hashing(**kwargs))
        os.makedirs(self.instance_saved_path, exist_ok=True)

        self.backbone = LLM(model_name=backbone_llm_model_name)
        self.num = num
        self.use_cache=use_cache
        self.criteria = criteria
        self.debug=debug

        self.test_cases = []
        self.max_retry = self.num * 2
        self.test_fn = self._test_fn

    @abstractmethod
    def _test_fn(self, ret):
        pass
    
    @abstractmethod
    def _generator(self):
        pass
    
    def set_settings(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def write_test_fixture_file(self, output_dir, **kwargs):
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(kwargs, f, indent=2, ensure_ascii=False)

    def _load_cached_test_cases(self):
        return None
    
    def run(self):
        """Run all generated test cases in this test case
        """
        passes = []
        fixtures, results = Munch(), Munch()
        for tc in self.test_cases:
            passed, fixture, result = self.test_fn(tc)
            passes.append(passed)
            for k, v in fixture.items():
                if k not in fixtures: fixtures[k] = []
                fixtures[k].append(v)
            for k, v in result.items():
                if k not in results: results[k] = []
                results[k].append(v)
        if not passes: detection_result = "UNDETERMINED"
        # Verify whether the number of passed test cases meets the criteria
        else: detection_result = True if np.sum(passes)/len(passes) >= self.criteria else False
        logging.info(f"Test Class `{self.name}` Total Test Cases: {len(passes)}, Passed: {np.sum(passes)}, Criteria: {self.criteria}")

        return np.array(passes), fixtures, results, detection_result, self.criteria
