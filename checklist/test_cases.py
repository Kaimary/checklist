import random
import os, re, json, hashlib
import numpy as np
from pathlib import Path
from munch import Munch
from colorama import Fore
from dotenv import load_dotenv
from abc import ABC, abstractmethod
from camel.societies import RolePlaying
from camel.utils import print_text_animated

from checklist.database_utils.db_opt import duplicate_sqlite_database, insert_rows_into_table
from checklist.database_utils.execution import execute_sql, validate_sql_query
from checklist.llm import CONFIGS, LLM
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.database_manager import DatabaseManager
from checklist.database_utils.db_catalog.csv_utils import load_tables_description
from checklist.database_utils.db_info import load_schema_with_examples, load_schema_with_simulated_examples
from checklist.database_utils.db_values.preprocess import _get_unique_values
from spinner import Spinner

load_dotenv(override=True)
TEST_INSTANCE_ROOT_PATH = Path(os.getenv("TEST_INSTANCE_ROOT_PATH"))

def hashing(schema, nl=None, sql=None):
    combined = schema
    if nl is not None: combined += f";{nl}"
    if sql is not None: 
        normalized_sql = re.sub(r"\s+", " ", sql.strip().lower())
        combined += f";{normalized_sql}"
    hashing_str = hashlib.md5(combined.encode()).hexdigest()[:8]
    
    return hashing_str

class TestCase(ABC):
    def __init__(self, name, nl, hint, sql, sql_dialect, db_id, db_path, num=-1, use_cache=True):
        self.name = name
        self.num = num
        self.nl=nl
        self.hint=hint
        self.sql=sql
        self.sql_dialect=sql_dialect
        self.db_id=db_id
        self.db_path=db_path
        self.use_cache=use_cache
        
        self.instances = []
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
    def _generator(self):
        pass

    def run(self):
        """Run all generated test cases in this unit test
        """
        passes = []
        fixtures, results = Munch(), Munch()
        for tc in self.instances:
            passed, fixture, result = self.test_fn(tc)
            passes.append(passed)
            for k, v in fixture.items():
                if k not in fixtures: fixtures[k] = []
                fixtures[k].append(v)
            for k, v in result.items():
                if k not in results: results[k] = []
                results[k].append(v)
                
        return np.array(passes), fixtures, results


class OracleResultTestCase(TestCase):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, num=10):
        super().__init__("Oracle Result Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")

        self.instance_saved_path = os.path.join(
            TEST_INSTANCE_ROOT_PATH, "oracle", "oracle_result", db_id, hashing(db_id, nl=nl))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        schema = DatabaseManager(db_id=self.db_id).get_db_schema() # type: ignore
        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=None, # type: ignore
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self.instances = self._generator()
                
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
    
    def _validate_test_fixture(self, response, history):
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
                'NUMERIC': float,
                'DATE': str
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
        def __check_response_history_compatible(response, history):
            def __dicts_equal___(d1, d2):
                if d1.keys() != d2.keys():
                    return False
                
                for key in d1:
                    v1, v2 = d1[key], d2[key]
                    # If both are lists, check order-insensitive equality
                    if isinstance(v1, list) and isinstance(v2, list):
                        # Convert inner lists to tuples (hashable) for set comparison
                        set1 = set(tuple(item) for item in v1)
                        set2 = set(tuple(item) for item in v2)
                        if set1 != set2:
                            return False
                    else:
                        if v1 != v2:
                            return False
                return True
            
            for h in history:
                if not __dicts_equal___(response["database_instances"], h["data"]): continue
                # Check whether result is the same or not if instances are same. If it is the case, drop it
                if __dicts_equal___(response["resulting_data"], h["label_result"]): return False
                # Otherwise, double check which `result` is the correct one
                prompt = get_prompt(template_name="oracle_result_checking", schema_string=self.schema_string)
                parser = get_parser(parser_name="oracle_result_checking")
                response = self.GPT4o(prompt, parser, request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl,
                    "INSTANCES": json.dumps(h['data'], indent=4),
                    "RESULT1": json.dumps(h['label_result'], indent=4),
                    "RESULT2": json.dumps(response["resulting_data"], indent=4)
                })
                # Modify the `result` according to the output (TODO further check its correctness?)
                h['label_result'] = response["resulting_data"]
                return False
            
            return True
        
        # Check required keys exist
        if "database_instances" not in response.keys() or "resulting_data" not in response.keys(): return False
        # Check table names exist
        table_names = DatabaseManager().get_db_all_tables()
        if not __check_table_names(response["database_instances"].keys(), table_names): return False
        # Check data types consistent
        column_types= DatabaseManager().get_all_column_types()
        if not __check_data_types(response["database_instances"], column_types): return False
        # Check if duplicate response generated
        if not __check_response_history_compatible(response, history): return False
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
        
    def _form_instance(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: <`db-file-with-generated-data`, `to-executed-sql`, `expected-executed-result`>
        
        Parameters
        ----------
        ret: Dict with `data` and `result` keys
        No return value
        """
        TEST_INSTANCE_ROOT_PATH = os.path.join(self.instance_saved_path, f"{idx}")
        os.makedirs(TEST_INSTANCE_ROOT_PATH, exist_ok=True)
        
        # Create test data instances
        ret.test_fixtures.db = os.path.join(TEST_INSTANCE_ROOT_PATH, f"{self.db_id}.sqlite")
        duplicate_sqlite_database(self.db_path, ret.test_fixtures.db, reset=True)
        for t, rows in ret.test_fixtures.data.items(): insert_rows_into_table(ret.test_fixtures.db, table_name=t, rows=rows)
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, 
            database=ret.test_fixtures.db, sql=self.sql, expect=ret.test_fixtures.label_result)
        
        return ret
    
    def _generator(self, verbose=True):
        def __history_to_string(history):
            return "\n".join(
                f"--- Example {i+1} ---\n"
                f"database_instances: {json.dumps(h['data'], indent=4)}\n\n"
                # f"resulting_data: {json.dumps(h['label_result'], indent=4)}"
                for i, h in enumerate(history)
            )

        parser = get_parser(parser_name="oracle_data_generation")
        history, outputs = [], []
        
        spinner = Spinner(f"Generating Test Case `{self.name}` test instances ...")
        with spinner:
            while(len(outputs) < self.num):
                ret = Munch()
                ret.test_fixtures = Munch()
                prompt = get_prompt(
                    template_name="oracle_data_generation", 
                    schema_string=self.schema_string,
                    history_string=__history_to_string(history) if history else None
                )
                response = self.GPT4o(prompt, parser, request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl
                    }
                )
                if not self._validate_test_fixture(response, history): 
                    if verbose: print(f"Generated test fixture validation failed! Retry...")
                    continue
                ret.test_fixtures.data = response["database_instances"]
                ret.test_fixtures.label_result = response["resulting_data"]
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test instances ...")
        return outputs

class NLRelaxTestCase(TestCase):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, num=10):
        super().__init__("Natural Language Relexing Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "metamorphic", "nl_relax", db_id, hashing(db_id, nl, sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)

        self.instances = self._generator()
        
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
    
    def _validate_test_fixture(self, response):
        # def __check_response_history_compatible(response, history):
        #     if any(h.nl_mutant == response["nl_mutant"] or h.sql_mutant == response["sql_mutant"] for h in history): return False
        #     return True
        
        # Check if the mutanted SQL is valid
        if validate_sql_query(self.db_path, response["sql_mutant"])["STATUS"] != "OK": return False
        ## Check if duplicate response generated
        # if not __check_response_history_compatible(response, history): return False
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
        
    def _form_instance(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: <`MT-type`, `description`, `db-file`, `original-nl`, `original-sql`, `nl-mutant`, `sql-mutant`>
        
        Parameters
        ----------
        ret: Dict with `type`, `desc`, `nl-mutant`, `sql-mutant` keys
        No return value
        """
        TEST_INSTANCE_ROOT_PATH = os.path.join(self.instance_saved_path, f"{idx}")
        os.makedirs(TEST_INSTANCE_ROOT_PATH, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, 
            type=ret.type,
            desc=ret.desc,
            database=self.db_path, 
            nl=self.nl,
            sql=self.sql, 
            nl_mutant=ret.test_fixtures.nl_mutant,
            sql_mutant=ret.test_fixtures.sql_mutant)
        
        return ret
    
    def _generator(self, verbose=True):
        def __history_to_string(history):
            return "\n".join(
                f"--- Example {i+1} ---\n"
                f"nl mutation: {h.nl_mutant}\n\n"
                f"sql mutation: {h.sql_mutant}\n\n"
                for i, h in enumerate(history)
            )   
        def __error_to_string(invalids):
            return "\n".join(
                f"sql mutation: {sql}\n\n"
                for sql in invalids
            )
        
        parser = get_parser(parser_name="nl_relaxing_generation")
        history, outputs = [], []
        invalids = set()
        spinner = Spinner(f"Generating Test Case `{self.name}` test instances ...")
        with spinner:
            while(len(outputs) < self.num):
                ret = Munch()
                ret.test_fixtures = Munch()
                prompt = get_prompt(
                    template_name="nl_relaxing_generation", 
                    invalid_queries_string=__error_to_string(invalids) if invalids else None,
                    history_string=__history_to_string(history) if history else None
                )
                response = self.GPT4o(prompt, parser, 
                    request_kwargs={
                        "HINT": self.hint,
                        "QUESTION": self.nl,
                        "QUERY": self.sql
                    }
                )
                if not self._validate_test_fixture(response):
                    invalids.add(response["sql_mutant"])
                    if verbose: print(f"Generated test fixture validation failed! Retry...")
                    continue
                ret.type = response["type"]
                ret.desc = response["description"]
                ret.test_fixtures.nl_mutant = response["nl_mutant"]
                ret.test_fixtures.sql_mutant = response["sql_mutant"] 
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test instances ...")
        return outputs

class NLStrengthenTestCase(TestCase):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, num=10):
        super().__init__("Natural Language Strengthening Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "metamorphic", "nl_strengthen", db_id, hashing(nl, sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.instances = self._generator()
        
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
    
    
    def _validate_test_fixture(self, response):
        # Check if the mutanted SQL is valid
        return validate_sql_query(self.db_path, response["sql_mutant"])["STATUS"] == "OK"
    
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
        
    def _form_instance(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: <`MT-type`, `description`, `db-file`, `original-nl`, `original-sql`, `nl-mutant`, `sql-mutant`>
        
        Parameters
        ----------
        ret: Dict with `type`, `desc`, `nl-mutant`, `sql-mutant` keys
        No return value
        """
        TEST_INSTANCE_ROOT_PATH = os.path.join(self.instance_saved_path, f"{idx}")
        os.makedirs(TEST_INSTANCE_ROOT_PATH, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, 
            type=ret.type,
            desc=ret.desc,
            database=self.db_path, 
            nl=self.nl,
            sql=self.sql, 
            nl_mutant=ret.test_fixtures.nl_mutant,
            sql_mutant=ret.test_fixtures.sql_mutant)
        
        return ret
    
    def _generator(self, verbose=True):
        def __history_to_string(history):
            return "\n".join(
                f"--- Example {i+1} ---\n"
                f"nl mutation: {h.nl_mutant}\n\n"
                f"sql mutation: {h.sql_mutant}\n\n"
                for i, h in enumerate(history)
            )

        def __error_to_string(invalids):
            return "\n".join(
                f"sql mutation: {sql}\n\n"
                for sql in invalids
            )
        
        parser = get_parser(parser_name="nl_strengthening_generation")
        history, outputs = [], []
        invalids = set()
        while(len(outputs) < self.num):
            ret = Munch()
            ret.test_fixtures = Munch()
            prompt = get_prompt(
                template_name="nl_strengthening_generation", 
                invalid_queries_string=__error_to_string(invalids) if invalids else None,
                history_string=__history_to_string(history) if history else None
            )
            response = self.GPT4o(prompt, parser, 
                request_kwargs={
                    "HINT": self.hint,
                    "QUESTION": self.nl,
                    "QUERY": self.sql
                }
            )
            if not self._validate_test_fixture(response):
                invalids.add(response["sql_mutant"])
                if verbose: print(f"Generated test fixture validation failed! Retry...")
                continue
            ret.type = response["type"]
            ret.desc = response["description"]
            ret.test_fixtures.nl_mutant = response["nl_mutant"]
            ret.test_fixtures.sql_mutant = response["sql_mutant"] 
            history.append(ret.test_fixtures)
            outputs.append(self._form_instance(len(outputs), ret))
        return outputs

class CrossModelTestCase(TestCase):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, num=3, active_llm_num=3):
        super().__init__("Majority Voting Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        self.active_llm_num = active_llm_num
        self.llm_pool = self._create_llm_pool()
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "differential", "majority_vote", db_id, hashing_nl_sql(nl, sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.dm = DatabaseManager(db_id=self.db_id)
        self.instances = self._generator()

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
        return vote >= len(pred_list) / 2
    
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
        
    def _form_instance(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: <`db-file`, `candidate-sqls`, `original-sql`>
        
        Parameters
        ----------
        ret: Dict with `candidates` key
        No return value
        """
        TEST_INSTANCE_ROOT_PATH = os.path.join(self.instance_saved_path, f"{idx}")
        os.makedirs(TEST_INSTANCE_ROOT_PATH, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, 
            candidates=ret.test_fixtures.candidates,
            sql=self.sql)
        
        return ret
    
    def _generator(self, verbose=True):
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
            outputs.append(self._form_instance(len(outputs), ret))
        return outputs

class SelfConsistencyTestCase(TestCase):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, model=None, num=10):
        super().__init__("Query Consistency Unit Test", nl, hint, sql, sql_dialect, db_id, db_path, num)
        if model is None:
            raise(Exception('No model provided. Please specify your NL2SQL model first'))
        self.model = model
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")
        
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "differential", "query_consistency", db_id, hashing_nl_sql(nl, sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)

        schema = DatabaseManager(db_id=self.db_id).get_db_schema() # type: ignore
        schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=schema_with_examples,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self.instances = self._generator()
        
    def set_settings(self, **kwargs):
        self.num = kwargs.get("num")
    
    def _compare_query_results(self, pred, target):
        if set(target) == set(pred):
            return True
        return False
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = execute_sql(self.db_path, ret.test_fixtures.predict_sql)
        ret.results.target = execute_sql(self.db_path, self.sql)
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results
    
    
    def _validate_test_fixture(self, response):
        # Check if the mutanted SQL is valid
        return validate_sql_query(self.db_path, response)["STATUS"] == "OK"
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "nl": kwargs.get("nl"),
            "sql": kwargs.get("sql"),
            "nl_mutant": kwargs.get("nl_mutant"),
            "predict_sql": kwargs.get("predict_sql")
        }
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
    def _form_instance(self, idx, ret):
        TEST_INSTANCE_ROOT_PATH = os.path.join(self.instance_saved_path, f"{idx}")
        os.makedirs(TEST_INSTANCE_ROOT_PATH, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, 
            nl=self.nl,
            sql=self.sql, 
            nl_mutant=ret.test_fixtures.nl_mutant,
            predict_sql=ret.test_fixtures.predict_sql)
        
        return ret
    
    def _generator(self, verbose=True):
        def __history_to_string(history):
            return "\n".join(
                f"--- Example {i+1} ---\n"
                f"nl mutation: {h.nl_mutant}\n\n"
                for i, h in enumerate(history)
            )
        
        parser = get_parser(parser_name="nl_mutation_generation")
        prompt2 = get_prompt(template_name="nl2sql_translation", schema_string=self.schema_string)
        parser2 = get_parser(parser_name="nl2sql_translation")
        history, outputs = [], []
        while(len(outputs) < self.num):
            ret = Munch()
            ret.test_fixtures = Munch()
            prompt = get_prompt(
                template_name="nl_mutation_generation",
                history_string=__history_to_string(history) if history else None
            )
            nl_mutant = self.GPT4o(prompt, parser, request_kwargs={
                "HINT": self.hint, 
                "QUESTION": self.nl
                }
            )["NL"]
            pred = self.model(prompt2, parser2, request_kwargs={
                "HINT": self.hint,
                "QUESTION": nl_mutant
                }
            )["SQL"]
            if not self._validate_test_fixture(pred): 
                if verbose: print(f"Generated test fixture validation failed! Retry...")
                continue
            ret.test_fixtures.nl_mutant = nl_mutant
            ret.test_fixtures.predict_sql = pred
            history.append(ret.test_fixtures)
            outputs.append(self._form_instance(len(outputs), ret))
        return outputs

class QueryReviewTestCase(TestCase):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path):
        super().__init__("Step-through Query Review Unit Test", nl, hint, sql, sql_dialect, db_id, db_path)
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")

        
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "explore", "query_review", db_id, hashing_nl_sql())
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.dm = DatabaseManager(db_id=self.db_id)
        self.instances = self._generator()
        
    def set_settings(self, **kwargs):
        pass

    def _compare_query_results(self, preds, targets):
        for pred, target in zip(preds, targets):
            if pred != target: return False
        return True
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = [turn['status'] for turn in ret.test_fixtures.turns]
        ret.results.target = ['Pass' for _ in ret.test_fixtures.turns]
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "turns": kwargs.get("turns"),
        }
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
    def _form_instance(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        
        Parameters
        ----------
        ret: Dict with `turns` key
        No return value
        """
        TEST_INSTANCE_ROOT_PATH = os.path.join(self.instance_saved_path, f"{idx}")
        os.makedirs(TEST_INSTANCE_ROOT_PATH, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, turns=ret.test_fixtures.turns)
        
        return ret
    
    def _load_cached_test_cases(self):
        
        return None
    
    def _generator(self, verbose=True):
        if self.use_cache:
            outputs = self._load_cached_test_cases()
        else:
            schema = DatabaseManager().get_db_schema() # type: ignore
            # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
            schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
            schema_string = DatabaseManager().get_database_schema_string(
                tentative_schema=schema,
                schema_with_examples=None,
                schema_with_descriptions=schema_with_descriptions,
                include_value_description=True
            )        
            outputs = []
            ret = Munch()
            ret.test_fixtures = Munch()
            task_prompt = ("Using Rubber Duck Debugging to verify the correctness of the SQL query clause by clause\n"
                f"{self.sql}"
                f"for natural language question \"{self.nl}\" under the database schema\n"
                f"{schema_string}"
            )
            role_play_session = RolePlaying(
                assistant_role_name="SQL Developer",
                assistant_agent_kwargs=dict(model=self.GPT4o),
                user_role_name="Rubber Duck Debugging Assistant",
                user_agent_kwargs=dict(model=self.GPT4o),
                task_prompt=task_prompt,
                with_task_specify=False,
                #   task_specify_agent_kwargs=dict(model=model),
            )
            
            n = 0
            chat_turn_limit = 10
            input_msg = role_play_session.init_chat()
            turns = []
            # Turn-based simulation
            while n < chat_turn_limit:
                n += 1
                assistant_response, user_response = role_play_session.step(input_msg)
                if assistant_response.terminated:
                    print(
                        Fore.GREEN
                        + (
                            "AI Assistant terminated. Reason: "
                            f"{assistant_response.info['termination_reasons']}."
                        )
                    )
                    break
                if user_response.terminated:
                    print(
                        Fore.GREEN
                        + (
                            "AI User terminated. "
                            f"Reason: {user_response.info['termination_reasons']}."
                        )
                    )
                    break

                # print_text_animated(Fore.BLUE + f"AI User:\\n\\n{user_response.msg.content}\\n")
                # print_text_animated(
                #     Fore.GREEN + "AI Assistant:\\n\\n"
                #     f"{assistant_response.msg.content}\\n"
                # )
                
                parsed_response = json.loads(assistant_response.msg.content.strip())
                if "CAMEL_TASK_DONE" in user_response.msg.content: break
                turns.append(parsed_response)
                input_msg = assistant_response.msg
        

            ret.test_fixtures.turns = turns
            outputs.append(self._form_instance(len(outputs), ret))
        
        return outputs

class NLReviewTestCase(TestCase):
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path):
        super().__init__("Step-through Natural Language Review Unit Test", nl, hint, sql, sql_dialect, db_id, db_path)
        self.GPT4o = LLM(model_name="gpt-4o-mini-0708")

        
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "explore", "nl_review", db_id, hashing_nl_sql(nl, sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.dm = DatabaseManager(db_id=self.db_id)
        self.instances = self._generator()
        
    def set_settings(self, **kwargs):
        pass

    def _compare_query_results(self, preds, targets):
        for pred, target in zip(preds, targets):
            if pred != target: return False
        return True
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = [turn['status'] for turn in ret.test_fixtures.turns]
        ret.results.target = ['Pass' for _ in ret.test_fixtures.turns]
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "turns": kwargs.get("turns"),
        }
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
        
    def _form_instance(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        
        Parameters
        ----------
        ret: Dict with `turns` key
        No return value
        """
        TEST_INSTANCE_ROOT_PATH = os.path.join(self.instance_saved_path, f"{idx}")
        os.makedirs(TEST_INSTANCE_ROOT_PATH, exist_ok=True)
        
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, turns=ret.test_fixtures.turns)
        
        return ret
    
    def _generator(self, verbose=True):
        schema = DatabaseManager().get_db_schema() # type: ignore
        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=None,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )        
        outputs = []
        
        ret = Munch()
        ret.test_fixtures = Munch()
        
        task_prompt = ("Using Rubber Duck Debugging to verify the correctness of the below natural language question phrase by phrase\n"
            f"{self.nl}"
            f"against the SQL query\n"
            f"{self.sql}"
            "under the database schema:\n"
            f"{schema_string}"
        )
        role_play_session = RolePlaying(
            assistant_role_name="SQL Developer",
            assistant_agent_kwargs=dict(model=self.GPT4o),
            user_role_name="Rubber Duck Debugging Assistant",
            user_agent_kwargs=dict(model=self.GPT4o),
            task_prompt=task_prompt,
            with_task_specify=False,
            #   task_specify_agent_kwargs=dict(model=model),
        )
        
        n = 0
        chat_turn_limit = 10
        turns = []
        input_msg = role_play_session.init_chat()
        # Turn-based simulation
        while n < chat_turn_limit:
            n += 1
            assistant_response, user_response = role_play_session.step(input_msg)
            if assistant_response.terminated:
                print(
                    Fore.GREEN
                    + (
                        "AI Assistant terminated. Reason: "
                        f"{assistant_response.info['termination_reasons']}."
                    )
                )
                break
            if user_response.terminated:
                print(
                    Fore.GREEN
                    + (
                        "AI User terminated. "
                        f"Reason: {user_response.info['termination_reasons']}."
                    )
                )
                break

            # print_text_animated(Fore.BLUE + f"AI User:\\n\\n{user_response.msg.content}\\n")
            # print_text_animated(
            #     Fore.GREEN + "AI Assistant:\\n\\n"
            #     f"{assistant_response.msg.content}\\n"
            # )
            
            parsed_response = json.loads(assistant_response.msg.content.strip())
            turns.append(parsed_response)
            if "CAMEL_TASK_DONE" in user_response.msg.content: break
            
            input_msg = assistant_response.msg
        
        ret.test_fixtures.turns = turns
        outputs.append(self._form_instance(len(outputs), ret))
        
        return outputs