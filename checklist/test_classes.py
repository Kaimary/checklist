import copy, logging
import os, re, json, hashlib, random
import numpy as np
from pathlib import Path
from munch import Munch
from colorama import Fore, Style
from dotenv import load_dotenv
from abc import ABC, abstractmethod
from camel.utils import print_text_animated
from camel.societies import RolePlaying
from camel.models import ModelFactory
from camel.configs import ChatGPTConfig
from camel.types import ModelPlatformType, ModelType

from checklist.database_utils.schema import DatabaseSchema
from checklist.database_utils.schema_generator import DatabaseSchemaGenerator
from checklist.red.parser.report import BugLevel
from checklist.red.parser.schema import Schema
from checklist.red.parser.red_parser import Query
from checklist.spinner import Spinner
from checklist.llm import CONFIGS, LLM
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.database_manager import DatabaseManager
from checklist.database_utils.db_opt import create_sqlite_database, duplicate_sqlite_database, insert_rows_into_table
from checklist.database_utils.execution import execute_sql, validate_sql_query
from checklist.database_utils.db_catalog.csv_utils import load_tables_description
from checklist.database_utils.db_info import get_db_schema_from_json, load_schema_with_examples
from checklist.database_utils.db_values.preprocess import _get_unique_values


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

class ValidationError(Exception):
    pass

class TestClass(ABC):
    def __init__(self, name, nl, hint, sql, db_id, db_root_path, 
                 backbone_llm_model_name="gpt-4o-mini-0708", num=1, criteria=1.0, use_cache=False):
        self.name = name
        self.num = num
        self.nl=nl
        self.hint=hint
        self.sql=sql
        self.db_id=db_id
        self.db_root_path=db_root_path
        self.backbone_llm_model_name = backbone_llm_model_name
        self.use_cache=use_cache
        self.criteria = criteria

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
    def _generator(self):
        pass

    def _load_cached_test_cases(self):
        return None
    
    def run(self):
        """Run all generated test test_cases in this test case
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
        # Verify whether the number of passes for the test test_cases meets the test case criteria
        detection_result = True if np.sum(passes)/len(passes) >= self.criteria else False

        return np.array(passes), fixtures, results, detection_result

class SemanticCheckTestClass(TestClass):
    def __init__(self, schema_file_path, **kwargs):
        super().__init__("Semantic Check Test Class", **kwargs)

        self.instance_saved_path = os.path.join(
            TEST_INSTANCE_ROOT_PATH, "semantic", "semantic_check", self.db_id, hashing(self.db_id, sql=self.sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.db_path = os.path.join(self.db_root_path, self.db_id, f"{self.db_id}.sqlite")
        self.schema = Schema(get_db_schema_from_json(self.db_id, schema_file_path), self.db_path)
        self.test_cases = self._generator()

    def set_settings(self, **kwargs):
        self.criteria = kwargs.get("criteria", 1.0)

    def _compare_query_results(self, pred):
        if pred: return False
        return True
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.warnings = [bug for bug in ret.test_fixtures.bugs if type(bug) != str and bug.level == BugLevel.WARNING]
        ret.results.pred = [bug for bug in ret.test_fixtures.bugs if type(bug) == str or bug.level == BugLevel.ERROR]
        ret.results.standard = "pred is empty"
        passed = self._compare_query_results(ret.results.pred)
        return passed, ret.test_fixtures, ret.results
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "database": kwargs.get("database"),
            "sql": kwargs.get("sql"),
            "bugs": '\n'.join(str(bug) for bug in kwargs.get("bugs"))
        }
        output_path = os.path.join(output_dir, 'meta.json')
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
    def _form_instance(self, idx, ret):
        """
        Form each single test case, and save related test fixture for serialization. 
        Format as: a list of `bugs`
        
        Parameters
        ----------
        ret: Dict with `data` and `result` keys
        No return value
        """
        TEST_INSTANCE_ROOT_PATH = os.path.join(self.instance_saved_path, f"{idx}")
        os.makedirs(TEST_INSTANCE_ROOT_PATH, exist_ok=True)
        
        # Create test data test_cases
        ret.test_fixtures.db = os.path.join(TEST_INSTANCE_ROOT_PATH, f"{self.db_id}.sqlite")
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, 
            database=ret.test_fixtures.db, sql=self.sql, bugs=ret.test_fixtures.bugs)
        
        return ret
    
    def _generator(self):
        bugs, outputs = [], []
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            ret = Munch()
            ret.test_fixtures = Munch()
            try:
                parsed_query = Query(self.sql, copy.deepcopy(self.schema))
            except Exception as e:
                print(e)
                bugs.append(f"{e} SQL parse failed! \nSQL: {self.sql}")
            if parsed_query:
                try:
                    bugs.extend(parsed_query.validate())
                except Exception as e:
                    bugs.append(f"{e} Query validation process failed. \nSQL: {self.sql}")

            ret.test_fixtures.bugs = bugs
            outputs.append(self._form_instance(len(outputs), ret))
            del parsed_query

        return outputs

class OracleResultTestClass(TestClass):
    def __init__(self, prunned_threshold=20, **kwargs):
        super().__init__("Oracle Result Test Class", **kwargs)
        self.backbone = LLM(model_name=self.backbone_llm_model_name)

        self.instance_saved_path = os.path.join(
            TEST_INSTANCE_ROOT_PATH, "oracle", "oracle_result", self.db_id, hashing(self.db_id, nl=self.nl))
        os.makedirs(self.instance_saved_path, exist_ok=True)

        self.db_path = os.path.join(self.db_root_path, self.db_id, f"{self.db_id}.sqlite")
        self.schema = DatabaseManager(db_id=self.db_id, db_root_path=self.db_root_path).get_db_schema() # type: ignore
        self.schema_pruned = False
        # Prune the `lenthy` schema first, to ensure the quality of generated data 
        if any(len(cols) > prunned_threshold for cols in self.schema.values()):
            logging.warning(f"Database {self.db_id} has tables with more than {prunned_threshold} columns. Truncating the schema before generation ...")
            history = [] # Append the error messages to avoid endless llm loop
            parser = get_parser(parser_name="schema_pruning")
            prompt = get_prompt(template_name="schema_pruning", history_string='\n'.join(history) if history else None)
            while True:
                response = self.backbone(prompt, parser, request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl,
                    "DATABASE_SCHEMA": json.dumps(self.schema, indent=4)
                    }
                )
                try:
                    self._validate_pruned_schema(response)
                    self.schema = response    
                    self.schema_pruned = True
                    break
                except ValidationError as e:
                    history.append(str(e).split('.')[-1])
                    logging.warning(f"Pruned schema validation failed: {e}. Retrying...")
                
        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=self.schema,
            schema_with_examples=None, # type: ignore
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self.test_cases = self._generator()
                
    def set_settings(self, **kwargs):
        self.num = kwargs.get("num", self.num)
        self.criteria = kwargs.get("criteria", 1.0)

    def _compare_query_results(self, preds, raw_golds):
        normalized = {
            key: value if isinstance(value, list) else [value] 
            for key, value in raw_golds.items()
        }
        golds = list(zip(*normalized.values()))
        if preds and set(preds) == set(golds):
            return True
        return False
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        # ret.description = "Test the original SQL over a faked database with expected execution results"
        res = validate_sql_query(ret.test_fixtures.db, self.sql)
        logging.info(f"Validating SQL: {self.sql}\nResult: {res}")
        ret.results.pred = res['RESULT'] if res['STATUS'] == 'OK' else None
        ret.results.target = ret.test_fixtures.label_result
        logging.info(f"Predicted Result: {ret.results.pred}\nTarget Result: {ret.results.target}")
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results
    
    def _validate_pruned_schema(self, response):
        schema_generator = DatabaseSchemaGenerator(
            tentative_schema=DatabaseSchema.from_schema_dict(response), 
            db_id=self.db_id,
            db_path=self.db_path
        )
        ddl_commands = schema_generator._extract_create_ddl_commands()
        for table_name, ddl_command in ddl_commands.items():
            ddl_command = re.sub(r'\s+', ' ', ddl_command.strip())
            create_table_match = re.match(r'CREATE TABLE "?`?([\w -]+)`?"?\s*\((.*)\)', ddl_command, re.DOTALL)
            table = create_table_match.group(1).strip()
            if table != table_name:
                logging.warning(f"Table name mismatch: {table} != {table_name}")
            column_definitions = create_table_match.group(2).strip()
            definitions = DatabaseSchemaGenerator._separate_column_definitions(column_definitions)
            for col in response[table_name]:
                if all(col not in d for d in definitions):
                    raise ValidationError(
                        f"Pruned schema column name checking failed. "
                        f"Invalid column (in table {table_name}) found in pruned schema: {col}"
                    )
            # extra add primary key columns if not included
            for column_def in definitions:
                column_def = column_def.strip()
                if "primary key" in column_def.lower():
                    column_name_match = re.match(r"`([^`]+)`|(\w+)", column_def)
                    pk_column_name = (column_name_match.group(1) or column_name_match.group(2)).strip()
                    if pk_column_name not in response[table_name]:
                        # assuming the primary key column is the first column
                        response[table_name].insert(0, pk_column_name)
            return True                 

    def _validate_test_fixture(self, response, history):
        """Validate the correctness (check if the schema matches, and the types match) of the output from LLM

        Parameters
        ----------
        """
        def __extract_column_types_from_schema_string(schema_string):
            res = {}
            ddl_regex = re.compile(r"CREATE TABLE.*?\);", re.DOTALL | re.IGNORECASE)
            ddl_commands = ddl_regex.findall(schema_string)
            for ddl_command in ddl_commands:
                create_table_match = re.match(r'CREATE TABLE "?`?([\w -]+)`?"?\s*\((.*)\)', ddl_command, re.DOTALL)
                table_name = create_table_match.group(1).strip()
                column_definitions = create_table_match.group(2).strip()
                definitions = DatabaseSchemaGenerator._separate_column_definitions(column_definitions)
                type_regex = re.compile(r'.*\b(TEXT|INTEGER|REAL|NUMERIC|BLOB|BOOLEAN|DATE|DATETIME)\b', re.IGNORECASE)
                types = []
                for column_def in definitions:
                    column_def = column_def.strip()
                    if 'foreign key' in column_def.lower(): continue
                    match = type_regex.search(column_def)
                    if match:
                        types.append(match.group(1).upper())
                res[table_name] = types
            return res
        def __output_format_check(response):
            if "database_instances" not in response.keys() or "resulting_data" not in response.keys(): 
                raise ValidationError(
                    f"Output format check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `database_instances`, `resulting_data`"
                )
            return True
        def __schema_data_alignment_check(response, tables, column_types, schema):
            # table name validity check
            tables_in_data = response["database_instances"].keys()
            for td in tables_in_data:
                if td not in tables:
                    raise ValidationError(
                        f"Table name checking failed. "
                        f"Non-existed table name found in generated data: {td} "
                        f"Existing table names: {','.join(tables)}"
                    )
            # column count and data types consistent check
            sqlite_type_map = {
                'INTEGER': int,
                'REAL': float,
                'TEXT': str,
                'BLOB': bytes,
                'NUMERIC': float,
                'DATE': str
            }
            data = response["database_instances"]
            for t, rows in data.items():
                if not rows: continue
                if len(column_types[t]) != len(rows[0]):
                    raise ValidationError(
                        f"Schema-data column count mismatch. "
                        f"Column count in data row: {len(rows[0])}(e.g., {rows[0]}), "
                        f"Expected column count of table {t}: {len(column_types[t])}({','.join(schema[t])})"
                    )
                
                for v, tp in zip(rows[0], column_types[t]):
                    expected_type = sqlite_type_map[tp]
                    try:
                        expected_type(v)
                    except (ValueError, TypeError):
                        raise ValidationError(
                            f"Schema-data column type mismatch. "
                            f"Column type Data : {len(rows[0])} "
                            f"Expected column count of Table {t}: {len(tables[t])}"
                        )
            return True
        def __response_history_compatible_check(response, history):
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
                # Check whether result is the same or not if test cases are same. If it is the case, drop it
                if __dicts_equal___(response["resulting_data"], h["label_result"]): 
                    raise ValidationError(
                        f"Resulting data under same test case mismatch. "
                        f"Previous resulting data: {h['label_result']} "
                        f"Response resulting data: {response['resulting_data']}"
                    )
                # Otherwise, double check which `result` is the correct one
                prompt = get_prompt(template_name="oracle_result_checking", schema_string=self.schema_string)
                parser = get_parser(parser_name="oracle_result_checking")
                response = self.backbone(prompt, parser, request_kwargs={
                    "HINT": self.hint,
                    "QUESTION": self.nl,
                    "test_cases": json.dumps(h['data'], indent=4),
                    "RESULT1": json.dumps(h['label_result'], indent=4),
                    "RESULT2": json.dumps(response["resulting_data"], indent=4)
                })
                # Modify the `result` according to the output (TODO further check its correctness?)
                h['label_result'] = response["resulting_data"]
                raise ValidationError(f"Duplicate response detected.")
            return True
        
        # output format check
        __output_format_check(response)
        # schema-data alignment check
        table_names = DatabaseManager().get_db_all_tables() if not self.schema_pruned else [k for k in self.schema.keys()]
        column_types= DatabaseManager().get_all_column_types() \
            if not self.schema_pruned else __extract_column_types_from_schema_string(self.schema_string)
        __schema_data_alignment_check(response, table_names, column_types, self.schema)
        # response duplication check
        __response_history_compatible_check(response, history)
    
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
        
        # Create test data test_cases
        ret.test_fixtures.db = os.path.join(TEST_INSTANCE_ROOT_PATH, f"{self.db_id}.sqlite")
        logging.info(f"Creating test database at \"{ret.test_fixtures.db}\" ...")
        if not self.schema_pruned:
            duplicate_sqlite_database(src_db_path=self.db_path, dest_db_path=ret.test_fixtures.db, reset=True)
        else:
            create_sqlite_database(ret.test_fixtures.db, self.schema_string)
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
        parser2 = get_parser(parser_name="oracle_data_verification")
        history, outputs = [], []
        
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while(len(outputs) < self.num):
                ret = Munch()
                ret.test_fixtures = Munch()
                prompt = get_prompt(
                    template_name="oracle_data_generation", 
                    schema_string=self.schema_string,
                    history_string=__history_to_string(history) if history else None
                )
                response = self.backbone(prompt, parser, request_kwargs={
                    "HINT": self.hint,
                    "QUESTION": self.nl
                    }
                )
                # if not self._validate_test_fixture(response, history): 
                #     if verbose: spinner.set_message(f"Generated test fixture validation failed! Retry...")
                #     continue
                prompt2 = get_prompt(
                    template_name="oracle_data_verification", 
                    schema_string=self.schema_string
                )
                response2 = self.backbone(prompt2, parser2, request_kwargs={
                    "HINT": self.hint,
                    "QUESTION": self.nl,
                    "DATABASE_INSTANCES": json.dumps(response["database_instances"], indent=4),
                    "EXECUTED_RESULT": json.dumps(response["resulting_data"], indent=4)
                    }
                )
                response["resulting_data"] = response2["resulting_data"]
                try:
                    self._validate_test_fixture(response, history)
                except ValidationError as e:
                    logging.warning(f"Test fixture validation failed: {e}. Retrying...")
                    if verbose: spinner.set_message(f"Test fixture validation failed! Retrying...")
                    continue
                ret.test_fixtures.data = response["database_instances"]
                ret.test_fixtures.label_result = response["resulting_data"]
                logging.info(f"Generated test fixture: \nDatabase Instances: {json.dumps(ret.test_fixtures.data, indent=4)}\nExpected Result: {json.dumps(ret.test_fixtures.label_result, indent=4)}")
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class NLRelaxTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Natural Language Relexing Test Class", **kwargs)
        self.backbone = LLM(model_name=self.backbone_llm_model_name)
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "metamorphic", "nl_relax", self.db_id, hashing(self.db_id, self.nl, self.sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)

        self.db_path = os.path.join(self.db_root_path, self.db_id, f"{self.db_id}.sqlite")
        self.test_cases = self._generator()
        
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
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.num * 2:
                ret = Munch()
                ret.test_fixtures = Munch()
                prompt = get_prompt(
                    template_name="nl_relaxing_generation", 
                    invalid_queries_string=__error_to_string(invalids) if invalids else None,
                    history_string=__history_to_string(history) if history else None
                )
                response = self.backbone(prompt, parser, 
                    request_kwargs={
                        "HINT": self.hint,
                        "QUESTION": self.nl,
                        "QUERY": self.sql
                    }
                )
                if not self._validate_test_fixture(response):
                    invalids.add(response["sql_mutant"])
                    retry += 1
                    if verbose: print(f"Generated test fixture validation failed! Retry#{retry} ...")
                    continue
                ret.type = response["type"]
                ret.desc = response["description"]
                ret.test_fixtures.nl_mutant = response["nl_mutant"]
                ret.test_fixtures.sql_mutant = response["sql_mutant"] 
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class NLStrengthenTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Natural Language Strengthening Test Class", **kwargs)
        self.backbone = LLM(model_name=self.backbone_llm_model_name)
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "metamorphic", "nl_strengthen", self.db_id, hashing(self.nl, self.sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.db_path = os.path.join(self.db_root_path, self.db_id, f"{self.db_id}.sqlite")
        self.test_cases = self._generator()
        
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
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.num * 2:
                ret = Munch()
                ret.test_fixtures = Munch()
                prompt = get_prompt(
                    template_name="nl_strengthening_generation", 
                    invalid_queries_string=__error_to_string(invalids) if invalids else None,
                    history_string=__history_to_string(history) if history else None
                )
                response = self.backbone(prompt, parser, 
                    request_kwargs={
                        "HINT": self.hint,
                        "QUESTION": self.nl,
                        "QUERY": self.sql
                    }
                )
                if not self._validate_test_fixture(response):
                    invalids.add(response["sql_mutant"])
                    retry += 1
                    if verbose: print(f"Generated test fixture validation failed! Retry#{retry} ...")
                    continue
                ret.type = response["type"]
                ret.desc = response["description"]
                ret.test_fixtures.nl_mutant = response["nl_mutant"]
                ret.test_fixtures.sql_mutant = response["sql_mutant"] 
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class CrossModelTestClass(TestClass):
    def __init__(self, nl, hint, sql, db_id, db_root_path, num=3, active_llm_num=3):
        super().__init__("Majority Voting Test Class", nl, hint, sql, db_id, db_root_path, num)
        self.active_llm_num = active_llm_num
        self.llm_pool = self._create_llm_pool()
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "differential", "majority_vote", db_id, hashing(db_id, nl=nl, sql=sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.db_path = os.path.join(db_root_path, db_id, f"{db_id}.sqlite")
        schema = DatabaseManager(db_id=self.db_id, db_root_path=db_root_path).get_db_schema() # type: ignore
        schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=schema_with_examples,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self.test_cases = self._generator()

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
    
    def _validate_test_fixture(self, candidates):
        if len(candidates) < self.active_llm_num: return False
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
        prompt = get_prompt(template_name="nl2sql_translation", schema_string=self.schema_string)
        parser = get_parser(parser_name="nl2sql_translation")
        outputs = []
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.num * 2:
                candidates = []
                ret = Munch()
                ret.test_fixtures = Munch()
                for model in random.sample(self.llm_pool, self.active_llm_num):
                    one_retry = 0
                    while True and one_retry < 3:
                        candidate = model(prompt, parser, request_kwargs={"HINT": self.hint, "QUESTION": self.nl})["SQL"]
                        if validate_sql_query(self.db_path, candidate)["STATUS"] == "OK": break
                        one_retry += 1
                    if one_retry < 3: candidates.append(candidate)
                if not self._validate_test_fixture(candidates):
                    retry += 1
                    if verbose: print(f"Generated test fixture validation failed! Retry#{retry} ...")
                    continue
                ret.test_fixtures.candidates = candidates
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class SelfConsistencyTestClass(TestClass):
    def __init__(self, model=None, **kwargs):
        super().__init__("Query Consistency Test Class", **kwargs)
        if model is None:
            raise(Exception('No model provided. Please specify your NL2SQL model first ...'))
        self.model = model
        self.backbone = LLM(model_name=self.backbone_llm_model_name)
        
        self.instance_saved_path = os.path.join(
            TEST_INSTANCE_ROOT_PATH, 
            "differential", "query_consistency", self.db_id, hashing(self.db_id, nl=self.nl, sql=self.sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)

        self.db_path = os.path.join(self.db_root_path, self.db_id, f"{self.db_id}.sqlite")
        schema = DatabaseManager(db_id=self.db_id, db_root_path=self.db_root_path).get_db_schema()
        schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=schema_with_examples,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self.test_cases = self._generator()
        
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
    
    def _validate_test_fixture(self, nl_mutant, pred, history):
        if any(h.nl_mutant == nl_mutant for h in history): return False
        if validate_sql_query(self.db_path, pred)["STATUS"] != "OK": return False
        return True
    
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
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.num * 2:
                ret = Munch()
                ret.test_fixtures = Munch()
                prompt = get_prompt(
                    template_name="nl_mutation_generation",
                    history_string=__history_to_string(history) if history else None
                )
                nl_mutant = self.backbone(prompt, parser, request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl
                    }
                )["NL"]
                if any(nl_mutant == h.nl_mutant for h in history):
                    retry += 1
                    if verbose: print(f"Generated test fixture validation failed! Retry#{retry} ...")
                    continue
                # TODO change to be a unified interface that should be implemented for a given NL2SQL model
                pred = self.model(prompt2, parser2, request_kwargs={
                    "HINT": self.hint,
                    "QUESTION": nl_mutant
                    }
                )["SQL"]
                if not self._validate_test_fixture(nl_mutant, pred, history):
                    retry += 1
                    if verbose: print(f"Generated test fixture validation failed! Retry#{retry} ...")
                    continue
                ret.test_fixtures.nl_mutant = nl_mutant
                ret.test_fixtures.predict_sql = pred
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class QueryReviewTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Step-through Query Review Test Class", **kwargs)
        self.backbone = ModelFactory.create(
            model_platform=ModelPlatformType.AZURE,
            model_type=ModelType.GPT_4O_MINI,
            model_config_dict=ChatGPTConfig().as_dict() # [Optional] the config for model
        )
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "explore", "query_review", self.db_id, hashing(self.db_id, nl=self.nl, sql=self.sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.db_path = os.path.join(self.db_root_path, self.db_id, f"{self.db_id}.sqlite")
        schema = DatabaseManager(db_id=self.db_id, db_root_path=self.db_root_path).get_db_schema() # type: ignore
        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=None,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self.test_cases = self._generator()
        
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
        if self.use_cache:
            outputs = self._load_cached_test_cases()
        else:   
            outputs = []
            ret = Munch()
            ret.test_fixtures = Munch()
            prompt = get_prompt(template_name="query_rubber_duck_debugging", schema_string=self.schema_string)
            task_prompt = prompt.invoke({
                "QUESTION": self.nl,
                "HINT": self.hint,
                "SQL": self.sql
                }
            ).messages[0].content
            # prompt = ("Using Rubber Duck Debugging to verify the correctness of the SQL query clause by clause\n"
            #     f"{self.sql}"
            #     f"for natural language question \"{self.nl}\" under the database schema\n"
            #     f"{self.schema_string}"
            # )
            role_play_session = RolePlaying(
                assistant_role_name="SQL Developer",
                assistant_agent_kwargs=dict(model=self.backbone),
                user_role_name="Rubber Duck Debugging Assistant",
                user_agent_kwargs=dict(model=self.backbone),
                task_prompt=task_prompt,
                with_task_specify=False,
                #   task_specify_agent_kwargs=dict(model=model),
            )

            # # Print initial system messages
            # print(Fore.GREEN + f"AI Assistant sys message:\\n{role_play_session.assistant_sys_msg}\\n" + Style.RESET_ALL)
            # print(Fore.BLUE + f"AI User sys message:\\n{role_play_session.user_sys_msg}\\n" + Style.RESET_ALL)
            # print(Fore.YELLOW + f"Original task prompt:\\n{task_prompt}\\n" + Style.RESET_ALL)
            # print(
            #     Fore.CYAN
            #     + "Specified task prompt:"
            #     + f"\\n{role_play_session.specified_task_prompt}\\n"
            #     + Style.RESET_ALL
            # )
            # print(Fore.RED + f"Final task prompt:\\n{role_play_session.task_prompt}\\n" + Style.RESET_ALL)
            n = 0
            chat_turn_limit = 10
            input_msg = role_play_session.init_chat()
            turns = []
            # Turn-based simulation
            while n < chat_turn_limit:
                n += 1
                assistant_response, user_response = role_play_session.step(input_msg)
                if assistant_response.terminated:
                    print(Fore.GREEN + f"AI Assistant terminated. Reason: {assistant_response.info['termination_reasons']}." + Style.RESET_ALL)
                    break
                if user_response.terminated:
                    print(Fore.GREEN + f"AI User terminated. Reason: {user_response.info['termination_reasons']}." + Style.RESET_ALL)
                    break
                
                # Disable printing animation as it really slows down the test
                # print_text_animated(Fore.BLUE + f"AI User:\\n\\n{user_response.msg.content}\\n" + Style.RESET_ALL)
                # print_text_animated(Fore.GREEN + f"AI Assistant:\\n\\n{assistant_response.msg.content}\\n" + Style.RESET_ALL)
                
                parsed_response = json.loads(assistant_response.msg.content.strip())
                if "CAMEL_TASK_DONE" in user_response.msg.content: break
                turns.append(parsed_response)
                input_msg = assistant_response.msg
        
            ret.test_fixtures.turns = turns
            outputs.append(self._form_instance(len(outputs), ret))

        return outputs

class NLReviewTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Step-through Natural Language Review Test Class", **kwargs)
        self.backbone = ModelFactory.create(
            model_platform=ModelPlatformType.AZURE,
            model_type=ModelType.GPT_4O_MINI,
            model_config_dict=ChatGPTConfig().as_dict() # [Optional] the config for model
        )
        
        self.instance_saved_path = os.path.join(TEST_INSTANCE_ROOT_PATH, "explore", "nl_review", self.db_id, hashing(self.db_id, nl=self.nl, sql=self.sql))
        os.makedirs(self.instance_saved_path, exist_ok=True)
        
        self.db_path = os.path.join(self.db_root_path, self.db_id, f"{self.b_id}.sqlite")
        schema = DatabaseManager(db_id=self.db_id, db_root_path=db_root_path).get_db_schema() # type: ignore
        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=None,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self.test_cases = self._generator()
        
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
        outputs = []
        ret = Munch()
        ret.test_fixtures = Munch()
        prompt = get_prompt(template_name="nl_rubber_duck_debugging", schema_string=self.schema_string)
        task_prompt = prompt.invoke({
            "QUESTION": self.nl,
            "SQL": self.sql
            }
        ).messages[0].content
        # prompt = ("Using Rubber Duck Debugging to verify the correctness of the below natural language question phrase by phrase\n"
        #     f"{self.nl}"
        #     f"against the SQL query\n"
        #     f"{self.sql}"
        #     "under the database schema:\n"
        #     f"{self.schema_string}"
        # )
        role_play_session = RolePlaying(
            assistant_role_name="SQL Developer",
            assistant_agent_kwargs=dict(model=self.backbone),
            user_role_name="Rubber Duck Debugging Assistant",
            user_agent_kwargs=dict(model=self.backbone),
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
                print(Fore.GREEN + f"AI Assistant terminated. Reason: {assistant_response.info['termination_reasons']}." + Style.RESET_ALL)
                break
            if user_response.terminated:
                print(Fore.GREEN + f"AI User terminated. Reason: {user_response.info['termination_reasons']}." + Style.RESET_ALL)
                break

            print_text_animated(Fore.BLUE + f"AI User:\\n\\n{user_response.msg.content}\\n" + Style.RESET_ALL)
            print_text_animated(Fore.GREEN + f"AI Assistant:\\n\\n{assistant_response.msg.content}\\n" + Style.RESET_ALL)
            
            parsed_response = json.loads(assistant_response.msg.content.strip())
            turns.append(parsed_response)
            if "CAMEL_TASK_DONE" in user_response.msg.content: break
            
            input_msg = assistant_response.msg
        
        ret.test_fixtures.turns = turns
        outputs.append(self._form_instance(len(outputs), ret))
        
        return outputs