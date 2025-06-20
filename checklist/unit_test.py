import random
import os, re, json, hashlib
import numpy as np
from pathlib import Path
from munch import Munch
from dotenv import load_dotenv
from abc import ABC, abstractmethod

from checklist.database_utils.db_opt import duplicate_sqlite_database, insert_rows_into_table
from checklist.database_utils.execution import execute_sql, validate_sql_query
from checklist.llm import CONFIGS, LLM
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.database_manager import DatabaseManager
from checklist.database_utils.db_catalog.csv_utils import load_tables_description
from checklist.database_utils.db_info import load_schema_with_examples
from checklist.database_utils.db_values.preprocess import _get_unique_values

load_dotenv(override=True)
TEST_CASE_ROOT_PATH = Path(os.getenv("TEST_CASE_ROOT_PATH"))

def hashing_nl_sql(nl, sql):
    normalized_sql = re.sub(r"\s+", " ", sql.strip().lower())
    combined = f"{nl}_{normalized_sql}".encode()
    hashing_str = hashlib.md5(combined).hexdigest()[:8]
    return hashing_str

class UnitTest(ABC):
    def __init__(self, name, nl, hint, sql, sql_dialect, db_id, db_path, num=-1):
        self.name = name
        self.num = num
        self.nl=nl
        self.hint=hint
        self.sql=sql
        self.sql_dialect=sql_dialect
        self.db_id=db_id
        self.db_path=db_path
        
        self.test_cases = []
        self.test_fn = self._test_fn
        
    
    @abstractmethod
    def set_settings(self, **kwargs):
        pass
    
    @abstractmethod
    def write_test_fixture_file(self, output_dir, **kwargs):
        pass
    
    @abstractmethod
    def _test_fn(self, ret):
        pass
    
    @abstractmethod
    def generator(self):
        pass

    def run(self):
        """Run all generated test cases in this unit test
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
                
        return np.array(passes), fixtures, results
    
class OracleResultUnitTest(UnitTest):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, num=10):
        super().__init__("Oracle Result Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")

        
        self.test_case_saved_path = os.path.join(TEST_CASE_ROOT_PATH, "oracle", "oracle_result", db_id, hashing_nl_sql(nl, sql))
        os.makedirs(self.test_case_saved_path, exist_ok=True)
        
        self.dm = DatabaseManager(db_id=self.db_id)
        self.test_cases = self.generator()
        
    def set_settings(self, **kwargs):
        self.num = kwargs.get("num")

    def _compare_query_results(self, preds, raw_golds):
        normalized = {
            key: value if isinstance(value, list) else [value] 
            for key, value in raw_golds.items()
        }
        golds = list(zip(*normalized.values()))
        if set(preds) == set(golds):
            return True
        return False
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        # ret.description = "Test the original SQL over a faked database with expected execution results"
        ret.results.pred = execute_sql(ret.test_fixtures.db, self.sql)
        ret.results.target = ret.test_fixtures.label_result
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results
    
    def _validate_test_fixture(self, ret):
        """Validate the correctness (check if the schema matches, and the types match) of the output from LLM

        Parameters
        ----------
        """
        def __check_table_names(data, tables): return all(d in tables for d in data)
        def __check_data_types(data, tables):
            sqlite_type_map = {
                'INTEGER': int,
                'REAL': float,
                'TEXT': str,
                'BLOB': bytes,
                'NUMERIC': float
            }
            for t, rows in data.items():
                if not rows: continue
                if len(tables[t]) != len(rows[0]): return False
                
                column_types = tables[t]
                for v, t in zip(rows[0], column_types):
                    expected_type = sqlite_type_map[t]
                    try:
                        expected_type(v)
                    except (ValueError, TypeError):
                        return False
            return True
        
        table_names = DatabaseManager().get_db_all_tables()
        if not __check_table_names(ret.test_fixtures.data.keys(), table_names): return False
        column_types= DatabaseManager().get_all_column_types()
        if not __check_data_types(ret.test_fixtures.data, column_types): return False
        
        return True
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "database": kwargs.get("database"),
            "sql": kwargs.get("sql"),
            "expect": kwargs.get("expect")
        }
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
    def _form_test_case(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: <`db-file-with-generated-data`, `to-executed-sql`, `expected-executed-result`>
        
        Parameters
        ----------
        ret: Dict with `data` and `result` keys
        No return value
        """
        test_case_root_path = os.path.join(self.test_case_saved_path, f"{idx}")
        os.makedirs(test_case_root_path, exist_ok=True)
        
        # Create test data instances
        ret.test_fixtures.db = os.path.join(test_case_root_path, f"{self.db_id}.sqlite")
        duplicate_sqlite_database(self.db_path, ret.test_fixtures.db, reset=True)
        for t, rows in ret.test_fixtures.data.items(): insert_rows_into_table(ret.test_fixtures.db, table_name=t, rows=rows)
        # test case serialization
        self.write_test_fixture_file(output_dir=test_case_root_path, 
            database=ret.test_fixtures.db, sql=self.sql, expect=ret.test_fixtures.label_result)
        
        return ret
    
    def generator(self, verbose=True):
        schema = DatabaseManager().get_db_schema() # type: ignore
        schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=schema_with_examples,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        prompt = get_prompt(template_name="oracle_data_generation", schema_string=schema_string)
        parser = get_parser(parser_name="oracle_data_generation")
        outputs = []
        while(len(outputs) < self.num):
            ret = Munch()
            ret.test_fixtures = Munch()
            response = self.GPT4o(prompt, parser, request_kwargs={"HINT": self.hint, "QUESTION": self.nl})
            ret.test_fixtures.data = response["database_instances"]
            ret.test_fixtures.label_result = response["resulting_data"]
            # ret.data = {
            #     "customers": [
            #         [1, None, 'EUR'],
            #         [2, None, 'EUR'],
            #         [3, None, 'CZK']
            #     ]
            # }
            # ret.result = {'ratio': 2}
            if not self._validate_test_fixture(ret): 
                if verbose: print(f"Generated test fixture validation failed! Retry...")
                continue
            outputs.append(self._form_test_case(len(outputs), ret))
        return outputs

class QueryRelaxUnitTest(UnitTest):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, num=10):
        super().__init__("Query Relexing Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")
        self.test_case_saved_path = os.path.join(TEST_CASE_ROOT_PATH, "metamorphic", "query_relax", db_id, hashing_nl_sql(nl, sql))
        os.makedirs(self.test_case_saved_path, exist_ok=True)

        self.test_cases = self.generator()
        
    def set_settings(self, **kwargs):
        self.num = kwargs.get("num")
    
    def _compare_query_results(self, orgin, mutant):
        return len(orgin) <= len(mutant)
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        # ret.description = "Test the original SQL over a faked database with expected execution results"
        ret.results.pred = execute_sql(self.db_path, ret.test_fixtures.sql_mutant)
        ret.results.target = execute_sql(self.db_path, self.sql)
        ret.results.standard = "len(pred) >= len(target)"
        passed = self._compare_query_results(ret.results.target, ret.results.pred)
        return passed, ret.test_fixtures, ret.results
    
    
    def _validate_test_fixture(self, ret):
        # Seems nothing need to be checked at this moment
        return True
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "type": kwargs.get("type"),
            "description": kwargs.get("desc"),
            "database": kwargs.get("database"),
            "nl": kwargs.get("nl"),
            "sql": kwargs.get("sql"),
            "nl_mutant": kwargs.get("nl_mutant"),
            "sql_mutant": kwargs.get("sql_mutant")
        }
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
    def _form_test_case(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: <`MT-type`, `description`, `db-file`, `original-nl`, `original-sql`, `nl-mutant`, `sql-mutant`>
        
        Parameters
        ----------
        ret: Dict with `type`, `desc`, `nl-mutant`, `sql-mutant` keys
        No return value
        """
        test_case_root_path = os.path.join(self.test_case_saved_path, f"{idx}")
        os.makedirs(test_case_root_path, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=test_case_root_path, 
            type=ret.type,
            desc=ret.desc,
            database=self.db_path, 
            nl=self.nl,
            sql=self.sql, 
            nl_mutant=ret.test_fixtures.nl_mutant,
            sql_mutant=ret.test_fixtures.sql_mutant)
        
        return ret
    
    def generator(self, verbose=True):
        prompt = get_prompt(template_name="query_relaxing_generation")
        parser = get_parser(parser_name="query_relaxing_generation")
        outputs = []
        while(len(outputs) < self.num):
            ret = Munch()
            ret.test_fixtures = Munch()
            response = self.GPT4o(prompt, parser, 
                request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl, 
                    "QUERY": self.sql
                }
            )
            ret.type = response["type"]
            ret.desc = response["description"]
            ret.test_fixtures.nl_mutant = response["nl_mutant"]
            ret.test_fixtures.sql_mutant = response["sql_mutant"]
            if not self._validate_test_fixture(ret): 
                if verbose: print(f"Generated test fixture validation failed! Retry...")
                continue
            outputs.append(self._form_test_case(len(outputs), ret))
        return outputs
    
class QueryStrengthenUnitTest(UnitTest):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, num=10):
        super().__init__("Query Strengthening Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")
        self.test_case_saved_path = os.path.join(TEST_CASE_ROOT_PATH, "oracle", "oracle_result", db_id, hashing_nl_sql(nl, sql))
        os.makedirs(self.test_case_saved_path, exist_ok=True)
        
        self.test_cases = self.generator()
        
    def set_settings(self, **kwargs):
        self.num = kwargs.get("num")
    
    def _compare_query_results(self, orgin, mutant):
        return len(orgin) >= len(mutant)
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = execute_sql(self.db_path, ret.test_fixtures.sql_mutant)
        ret.results.target = execute_sql(self.db_path, self.sql)
        ret.results.standard = "len(pred) <= len(target)"
        passed = self._compare_query_results(ret.results.target, ret.results.pred)
        return passed, ret.test_fixtures, ret.results
    
    
    def _validate_test_fixture(self, ret):
        # Check if the mutanted SQL is valid
        return validate_sql_query(self.db_path, ret.test_fixtures.sql_mutant)["STATUS"] == "OK"
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "type": kwargs.get("type"),
            "description": kwargs.get("desc"),
            "database": kwargs.get("database"),
            "nl": kwargs.get("nl"),
            "sql": kwargs.get("sql"),
            "nl_mutant": kwargs.get("nl_mutant"),
            "sql_mutant": kwargs.get("sql_mutant")
        }
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
    def _form_test_case(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: <`MT-type`, `description`, `db-file`, `original-nl`, `original-sql`, `nl-mutant`, `sql-mutant`>
        
        Parameters
        ----------
        ret: Dict with `type`, `desc`, `nl-mutant`, `sql-mutant` keys
        No return value
        """
        test_case_root_path = os.path.join(self.test_case_saved_path, f"{idx}")
        os.makedirs(test_case_root_path, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=test_case_root_path, 
            type=ret.type,
            desc=ret.desc,
            database=self.db_path, 
            nl=self.nl,
            sql=self.sql, 
            nl_mutant=ret.test_fixtures.nl_mutant,
            sql_mutant=ret.test_fixtures.sql_mutant)
        
        return ret
    
    def generator(self, verbose=True):
        prompt = get_prompt(template_name="query_strengthening_generation")
        parser = get_parser(parser_name="query_strengthening_generation")
        outputs = []
        while(len(outputs) < self.num):
            ret = Munch()
            ret.test_fixtures = Munch()
            response = self.GPT4o(prompt, parser, 
                request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl, 
                    "QUERY": self.sql
                }
            )
            ret.type = response["type"]
            ret.desc = response["description"]
            ret.test_fixtures.nl_mutant = response["nl_mutant"]
            ret.test_fixtures.sql_mutant = response["sql_mutant"]
            if not self._validate_test_fixture(ret): 
                if verbose: print(f"Generated test fixture validation failed! Retry...")
                continue
            outputs.append(self._form_test_case(len(outputs), ret))
        return outputs

class MajorityVoteUnitTest(UnitTest):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, num=3, active_llm_num=3):
        super().__init__("Majority Voting Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        self.active_llm_num = active_llm_num
        self.llm_pool = self._create_llm_pool()
        self.test_case_saved_path = os.path.join(TEST_CASE_ROOT_PATH, "differential", "majority_vote", db_id, hashing_nl_sql(nl, sql))
        os.makedirs(self.test_case_saved_path, exist_ok=True)
        
        self.dm = DatabaseManager(db_id=self.db_id)
        self.test_cases = self.generator()
        
    def set_settings(self, **kwargs):
        self.num = kwargs.get("num")
        self.active_llm_num = kwargs.get("active_llm_num")
        
    def _create_llm_pool(self):
        return [LLM(model_name=name) for name in list(CONFIGS.keys())]
    
    def _compare_query_results(self, pred_list, origin):
        vote = 0
        majority = pred_list[0]
        for pred in pred_list:
            if set(pred) == set(origin): vote+=1
        return vote >= len(pred_list) / 2, 
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = [execute_sql(self.db_path, candidate_sql) for candidate_sql in ret.test_fixtures.candidates]
        ret.results.target = execute_sql(self.db_path, self.sql)
        ret.results.standard = "majority(pred) == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results
    
    def _validate_test_fixture(self, ret):
        # Seems nothing need to be checked at this moment
        return True
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "candidates": kwargs.get("candidates"),
            "sql": kwargs.get("sql")
        }
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
    def _form_test_case(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: <`db-file`, `candidate-sqls`, `original-sql`>
        
        Parameters
        ----------
        ret: Dict with `candidates` key
        No return value
        """
        test_case_root_path = os.path.join(self.test_case_saved_path, f"{idx}")
        os.makedirs(test_case_root_path, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=test_case_root_path, 
            candidates=ret.test_fixtures.candidates,
            sql=self.sql)
        
        return ret
    
    def generator(self, verbose=True):
        schema = DatabaseManager().get_db_schema() # type: ignore
        schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=schema_with_examples,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        prompt = get_prompt(template_name="nl2sql_translation", schema_string=schema_string)
        parser = get_parser(parser_name="nl2sql_translation")
        outputs = []
        while(len(outputs) < self.num):
            ret = Munch()
            ret.test_fixtures = Munch()
            ret.test_fixtures.candidates = [model(prompt, parser, request_kwargs={"HINT": self.hint, "QUESTION": self.nl})["SQL"] \
                for model in random.sample(self.llm_pool, self.active_llm_num)
            ]
            if not self._validate_test_fixture(ret): 
                if verbose: print(f"Generated test fixture validation failed! Retry...")
                continue
            outputs.append(self._form_test_case(len(outputs), ret))
        return outputs
