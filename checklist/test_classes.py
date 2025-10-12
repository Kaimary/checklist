import os, re, json, random, copy, logging
import numpy as np
from munch import Munch
from colorama import Fore, Style
from camel.utils import print_text_animated
from camel.societies import RolePlaying
from camel.models import ModelFactory
from camel.configs import ChatGPTConfig
from camel.types import ModelPlatformType, ModelType

from checklist.spinner import Spinner
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.red.parser.report import BugLevel
from checklist.red.parser.schema import Schema
from checklist.red.parser.red_parser import Query
from checklist.database_manager import DatabaseManager
from checklist.base_test_class import TestClass, ValidationError
from checklist.database_utils.schema import DatabaseSchema
from checklist.database_utils.schema_generator import DatabaseSchemaGenerator
from checklist.models import CHESS, DAILSQL, RESDSQL, CODES15b, CODES7b, CSCSQL32b, CSCSQL7b, GenericLLM, OMNISQL32b
from checklist.database_utils.db_opt import create_sqlite_database, duplicate_sqlite_database, insert_rows_into_table
from checklist.database_utils.execution import execute_sql, validate_sql_query
from checklist.database_utils.db_catalog.csv_utils import load_tables_description
from checklist.database_utils.db_info import get_db_schema_from_json

class MinimumSyntaxTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Minimum Syntax Test Class", "minimum_syntax", "syntax", key="sql", **kwargs)
        self.test_cases = self._generator()
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.status = ret.test_fixtures.status
        ret.results.standard = "status is OK"
        passed = ret.results.status == "OK"
        return passed, ret.test_fixtures, ret.results
    
    def write_test_fixture_file(self, output_dir, **kwargs):
        data = {
            "sql": kwargs.get("sql"),
            "status": kwargs.get("status")
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
        
        # test case serialization
        self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, sql=self.sql, status=ret.test_fixtures.status)
        
        return ret
    
    def _generator(self):
        if self.use_cache: return self._load_cached_test_cases()
        
        outputs = []
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            ret = Munch()
            ret.test_fixtures = Munch()
            res = validate_sql_query(self.db_path, self.sql)
            ret.test_fixtures.status = res['STATUS']
            outputs.append(self._form_instance(len(outputs), ret))

        return outputs

class SemanticCheckTestClass(TestClass):
    def __init__(self, schema_file_path, **kwargs):
        super().__init__("Semantic Check Test Class", "semantic_check", "semantic", key="sql", **kwargs)

        self.schema = Schema(get_db_schema_from_json(self.db_id, schema_file_path), self.db_path)
        self.test_cases = self._generator()

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
        if self.use_cache: return self._load_cached_test_cases()
        
        bugs, outputs = [], []
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            ret = Munch()
            ret.test_fixtures = Munch()
            parsed_query = None
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
        super().__init__("Oracle Result Test Class", "oracle_result", "oracle", key="nl", **kwargs)

        self.schema = DatabaseManager(db_id=self.db_id, db_root_path=self.db_root_path).get_db_schema() # type: ignore
        self.schema_pruned = False
        # Prune the `lenthy` schema first, to ensure the quality of generated data 
        if any(len(cols) > prunned_threshold for cols in self.schema.values()):
            logging.warning(f"Database {self.db_id} has tables with more than {prunned_threshold} columns. Truncating the schema before generation ...")
            retry = 0
            error = set() # Append the error messages to avoid endless llm loop
            parser = get_parser(parser_name="schema_pruning")
            while True and retry < self.max_retry:
                prompt = get_prompt(template_name="schema_pruning", error_string='\n'.join(error) if error else None)
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
                    error.add(str(e).split('.')[-1])
                    retry += 1
                    logging.warning(f"Pruned schema validation failed: {e}. Retrying...")

        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=self.schema,
            schema_with_examples=None, # type: ignore
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self.max_retry = self.num * 3 # increase the max retry to 3 times of num for this test class
        self.test_cases = self._generator()

    def _compare_query_results(self, preds, oracles):
        # normalized = {
        #     key: value if isinstance(value, list) else [value] 
        #     for key, value in oracles.items()
        # }
        # golds = list(zip(*normalized.values()))
        def freeze(obj):
            """Recursively convert unhashable objects into hashable equivalents."""
            if isinstance(obj, dict):
                # sort keys to make it deterministic
                return tuple(sorted((k, freeze(v)) for k, v in obj.items()))
            elif isinstance(obj, (list, tuple, set)):
                return tuple(freeze(x) for x in obj)
            elif isinstance(obj, np.ndarray):
                return tuple(obj.tolist())  # turn ndarray into tuple of values
            else:
                return obj
        return bool(preds) and set(preds) == set(freeze(o) for o in oracles)
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        # Test the original SQL over a faked database with expected execution results
        res = validate_sql_query(ret.test_fixtures.db, self.sql)
        logging.info(f"Validating SQL: {self.sql}\nResult: {res['RESULT']}")
        ret.results.pred = res['RESULT'] if res['STATUS'] == 'OK' else None
        ret.results.target = ret.test_fixtures.label_result["rows"] if "rows" in ret.test_fixtures.label_result.keys() else []
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
                        f"Column `{col}` should not in table `{table_name}`❌"
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
            if not isinstance(response, dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"response type: {type(response)}, "
                    f"Expected type: dict"
                )
            if "database_instances" not in response.keys() or "resulting_data" not in response.keys(): 
                raise ValidationError(
                    f"Output format(key) check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `database_instances`, `resulting_data`"
                )
            if "unknown" not in response["resulting_data"].keys() and (any(k not in response["resulting_data"].keys() for k in ["columns", "rows"])): 
                raise ValidationError(
                    f"Output format(key in key) check failed. "
                    f"Keys found in `resulting_data`: {','.join(response['resulting_data'].keys())}, "
                    f"Expected keys: `unknown` or `columns` and `rows`"
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
                'INT': int,
                'INTEGER': int,
                'REAL': float,
                'TEXT': str,
                'BLOB': bytes,
                'NUMERIC': float,
                'DATE': str,
                'DATETIME': str
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
                            f"Expected column count of Table {t}: {len(column_types[t])}"
                        )
            return True
        def __response_history_compatible_check(response, history, max_retry):
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
                    raise ValidationError("Duplicate(`database_instances`+`resulting_data`) test case.")
                # Otherwise, double check which `result` is the correct one
                retry = 0
                prompt = get_prompt(template_name="oracle_result_checking", schema_string=self.schema_string)
                parser = get_parser(parser_name="oracle_result_checking")
                while True and retry < max_retry:
                    response2 = self.backbone(prompt, parser, request_kwargs={
                        "HINT": self.hint,
                        "QUESTION": self.nl,
                        "INSTANCES": json.dumps(h['data'], indent=4),
                        "RESULT1": json.dumps(h['label_result'], indent=4),
                        "RESULT2": json.dumps(response["resulting_data"], indent=4)
                    })
                    if isinstance(response2, dict):
                        if "resulting_data" in response2.keys(): break
                        if "columns" in response2.keys() and "rows" in response2.keys():
                            response2 = {"resulting_data": response2}
                            break
                # Modify the `result` according to the output (TODO further check its correctness?)
                h['label_result'] = response2["resulting_data"]
            return True
        
        # output format check
        __output_format_check(response)
        # schema-data alignment check
        table_names = DatabaseManager().get_db_all_tables() if not self.schema_pruned else [k for k in self.schema.keys()]
        column_types= DatabaseManager().get_all_column_types() \
            if not self.schema_pruned else __extract_column_types_from_schema_string(self.schema_string)
        __schema_data_alignment_check(response, table_names, column_types, self.schema)
        # response duplication check
        __response_history_compatible_check(response, history, self.max_retry)
       
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

        if self.use_cache: return self._load_cached_test_cases()

        parser = get_parser(parser_name="oracle_data_generation")
        parser2 = get_parser(parser_name="oracle_data_verification")
        history, outputs = [], []
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while(len(outputs) < self.num) and retry < self.max_retry:
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
                try:
                    self._validate_test_fixture(response, history)
                except ValidationError as e: 
                    logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                    if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                    retry += 1
                    continue
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
                if not isinstance(response2, dict):
                    retry += 1
                    continue
                if "columns" in response2.keys() and "rows" in response2.keys(): response2 = {"resulting_data": response2}
                response["resulting_data"] = response2["resulting_data"]
                try:
                    self._validate_test_fixture(response, history)
                except ValidationError as e:
                    logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                    if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                    retry += 1
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
        super().__init__("Natural Language Relaxing Test Class", "nl_relax", "metamorphic", **kwargs)
        self.test_cases = self._generator()

    def _compare_query_results(self, orgin, mutant):
        if not orgin or not mutant: return False
        return len(orgin) <= len(mutant)
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        # ret.description = "Test the original SQL over a faked database with expected execution results"
        ret.results.pred = execute_sql(self.db_path, ret.test_fixtures.sql_mutant)
        ret.results.target = None if validate_sql_query(self.db_path, self.sql)["STATUS"] != "OK" else execute_sql(self.db_path, self.sql)
        ret.results.standard = "len(pred) >= len(target)"
        passed = self._compare_query_results(ret.results.target, ret.results.pred)
        return passed, ret.test_fixtures, ret.results
    
    def _validate_test_fixture(self, response, history):
        def __response_history_compatible_check(response, history):
            if any(h.nl_mutant == response["nl_mutant"] or h.sql_mutant == response["sql_mutant"] for h in history):
                raise ValidationError(f"Duplicate response (nl/sql mutant) detected.")
            return True
        def __sql_executable_check(response, db_path):
            if not isinstance(response, dict) or "sql_mutant" not in response:
                raise ValidationError(
                        f"SQL executable check failed. "
                        f"Required key missing 'sql_mutant'"
                    )
            res = validate_sql_query(db_path, response["sql_mutant"])
            if res["STATUS"] != "OK":
                raise ValidationError(
                        f"SQL executable check failed. "
                        f"Fail log from DBMS: {res['RESULT']}"
                    )
            return True
        # mutanted SQL syntax check
        __sql_executable_check(response, self.db_path)
        # response duplication check
        __response_history_compatible_check(response, history)
       
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
                f"invalid sql mutation {idx+1}:\n{invalid[0]}\nerror:{invalid[1]}"
                for idx, invalid in enumerate(invalids)
            )
        
        if self.use_cache: return self._load_cached_test_cases()

        parser = get_parser(parser_name="nl_relaxing_generation")
        history, outputs = [], []
        invalids = set()
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.max_retry:
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
                try:
                    self._validate_test_fixture(response, history)
                except ValidationError as e:
                    retry += 1
                    logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                    if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                    invalids.add((response["sql_mutant"], str(e)))
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
        super().__init__("Natural Language Strengthening Test Class", "nl_strengthen", "metamorphic", **kwargs)
        self.test_cases = self._generator()

    def _compare_query_results(self, orgin, mutant):
        return len(orgin) >= len(mutant)
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = execute_sql(self.db_path, ret.test_fixtures.sql_mutant)
        ret.results.target = None if validate_sql_query(self.db_path, self.sql)["STATUS"] != "OK" else execute_sql(self.db_path, self.sql)
        ret.results.standard = "len(pred) <= len(target)"
        passed = self._compare_query_results(ret.results.target, ret.results.pred)
        return passed, ret.test_fixtures, ret.results
    
    def _validate_test_fixture(self, response, history):
        def __response_history_compatible_check(response, history):
            if any(h.nl_mutant == response["nl_mutant"] or h.sql_mutant == response["sql_mutant"] for h in history):
                raise ValidationError(f"Duplicate response (nl/sql mutant) detected.")
            return True
        def __sql_executable_check(response, db_path):
            res = validate_sql_query(db_path, response["sql_mutant"])
            if res["STATUS"] != "OK":
                raise ValidationError(
                        f"SQL executable check failed. "
                        f"Fail log from DBMS: {res['RESULT']}"
                    )
            return True
        # mutanted SQL syntax check
        __sql_executable_check(response, self.db_path)
        # response duplication check
        __response_history_compatible_check(response, history)
        
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
        
        if self.use_cache: return self._load_cached_test_cases()

        parser = get_parser(parser_name="nl_strengthening_generation")
        history, outputs = [], []
        invalids = set()
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.max_retry:
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
                try:
                    self._validate_test_fixture(response, history)
                except ValidationError as e:
                    retry += 1
                    logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                    if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                    invalids.add(response["sql_mutant"])
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
    def __init__(self, model_list=["cscsql", "chess", "omnisql", "gpt-4o-mini-0708"], active_model_num=3, **kwargs):
        super().__init__("Majority Voting Test Class", "majority_vote", "differential", **kwargs)
        self.active_model_num = active_model_num
        self.model_pool = self._create_nl2sql_model_pool(model_list)
        self.test_cases = self._generator()

    def _create_nl2sql_model_pool(self, model_list):
        MODEL_CLASS_MAP = {
            "cscsql7b": CSCSQL7b,
            "cscsql32b": CSCSQL32b,
            "chess": CHESS,
            "omnisql": OMNISQL32b,
            "resdsql": RESDSQL,
            "dailsql": DAILSQL,
            "codes15b": CODES15b,
            "codes7b": CODES7b
        }
        models = []
        for name in model_list:
            if name in MODEL_CLASS_MAP:
                models.append(MODEL_CLASS_MAP[name]())
            elif name.startswith("llm:"):
                # e.g. "llm:gpt-4o-mini-0708"
                _, model_name = name.split(":", 1)
                models.append(GenericLLM(model_name=model_name))
        return models
    
    def _compare_query_results(self, pred_list, origin):
        vote = 0
        majority = pred_list[0]
        for pred in pred_list:
            if set(pred) == set(origin): vote+=1
        return vote >= len(pred_list) / 2
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.standard = "majority(pred) == target"
        ret.results.pred = [execute_sql(self.db_path, candidate_sql) for candidate_sql in ret.test_fixtures.candidates]
        try:
            ret.results.target = execute_sql(self.db_path, self.sql)
            passed = self._compare_query_results(ret.results.pred, ret.results.target)
        except:
            ret.results.target = None
            passed = False
        return passed, ret.test_fixtures, ret.results
    
    def _validate_test_fixture(self, candidates):
        def __sql_executable_check(candidate, db_path):
            res = validate_sql_query(db_path, candidate)
            if res["STATUS"] != "OK":
                raise ValidationError(
                        f"SQL executable check failed. "
                        f"Fail log from DBMS: {res['RESULT']}"
                    )
            return True
        def __candidate_number_check(candidates, active_model_num):
            if len(candidates) < active_model_num:
                raise ValidationError(f"Candidate number check failed. Expected {active_model_num} candidates, but got {len(candidates)}.")
            return True
        # mutanted SQL syntax check
        if isinstance(candidates, str): __sql_executable_check(candidates, self.db_path)
        # candidate number check
        else: __candidate_number_check(candidates, self.active_model_num)
        
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
        def __error_to_string(invalids):
            return "\n".join(
                f"invalid sql {idx+1}:\n{invalid[0]}\nerror:{invalid[1]}"
                for idx, invalid in enumerate(invalids)
            )
        
        if self.use_cache: return self._load_cached_test_cases()

        prompt = get_prompt(template_name="nl2sql_translation", schema_string=self.schema_string)
        parser = get_parser(parser_name="nl2sql_translation")
        outputs = []
        invalids = set()
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.max_retry:
                candidates = []
                ret = Munch()
                ret.test_fixtures = Munch()
                for model in random.sample(self.model_pool, len(self.model_pool)):
                    one_retry = 0
                    while True and one_retry < 3:
                        if isinstance(model, GenericLLM):
                            prompt = get_prompt(
                                template_name="nl2sql_translation",
                                schema_string=self.schema_string,
                                invalid_queries_string=__error_to_string(invalids) if invalids else None
                            )
                            candidate = model(
                                prompt=prompt, 
                                parser=parser, 
                                request_kwargs={"HINT": self.hint, "QUESTION": self.nl}
                            )
                        else:
                            candidate = model(nl=self.nl)
                        try:
                            self._validate_test_fixture(candidate)
                            break
                        except ValidationError as e:
                            logging.warning(f"Candidate SQL validation failed: {e}")
                            if verbose: spinner.set_message(f"Candidate SQL validation failed: {e} ...")
                            if isinstance(model, GenericLLM):
                                invalids.add((candidate, str(e)))
                                one_retry += 1
                            else: 
                                one_retry = 3 # set larger than threshold
                                break
                    if one_retry < 3: 
                        candidates.append(candidate)
                        if len(candidates) == self.active_model_num: break
                # validate after getting all candidates
                try: 
                    self._validate_test_fixture(candidates)
                except ValidationError as e:
                    retry += 1
                    logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                    if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                    continue
                ret.test_fixtures.candidates = candidates
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class SelfConsistencyTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Query Consistency Test Class", "query_consistency", "differential", **kwargs)
        self.max_retry = self.num # hard-code the max retry to be the number of test cases
        self.cnt = 0
        self.nl_mutants = None
        self.nl_mutants_sql_outputs = None
        self.nl_mutants_saved_path = "spider_dev_nl_mutants.json"
        self.nl_mutants_sql_outputs_path = "codes_pred_nl_mutants.sql"
        # NOTICE!!! 
        # this test pre-checks whether there are predicted SQL outputs; 
        # If exists, will do the test; Otherwise, generate nl mutants and do fake testing.
        if os.path.exists(self.nl_mutants_sql_outputs_path):
            print(f"Predicted SQL outputs detected (`{self.nl_mutants_sql_outputs_path}`), ensure that you're aware of the behavior of using them...")
            with open(self.nl_mutants_saved_path) as f:
                self.nl_mutants = json.load(f)
            assert len(self.nl_mutants) % self.num == 0, f"The number of NL mutants ({len(self.nl_mutants)}) should be a multiple of the number of test cases ({self.num})"
            lines = open(self.nl_mutants_sql_outputs_path).readlines()
            self.nl_mutants_sql_outputs = lines[:self.num]
            with open(self.nl_mutants_sql_outputs_path, "w") as f:
                f.writelines(lines[self.num:])
        self.test_cases = self._generator()

    def _compare_query_results(self, pred, target):
        if pred and target and set(target) == set(pred):
            return True
        return False
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = None if validate_sql_query(self.db_path, ret.test_fixtures.predict_sql)["STATUS"] != "OK" else execute_sql(self.db_path, ret.test_fixtures.predict_sql)
        ret.results.target = None if validate_sql_query(self.db_path, self.sql)["STATUS"] != "OK" else execute_sql(self.db_path, self.sql)
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results
    
    def _validate_test_fixture(self, response, history):
        def __output_format_check(response):
            if not isinstance(response, dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"response type: {type(response)}, "
                    f"Expected type: dict"
                )
            if ("NL" not in response.keys() and "nl" not in response.keys()) and ("SQL" not in response.keys() and 'sql' not in response.keys()): 
                raise ValidationError(
                    f"Output format(key) check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `nl` or `sql`"
                )
            # normalize key name
            if "NL" in response.keys(): response["nl"] = response.pop("NL")
            if "SQL" in response.keys(): response["sql"] = response.pop("SQL")
            return True
        def __response_history_compatible_check(response, history):
            if any(h.nl_mutant == response["nl"] for h in history):
                raise ValidationError(f"Duplicate response (nl mutant) detected.")
            return True
        def __sql_executable_check(response, db_path):
            res = validate_sql_query(db_path, response["sql"])
            if res["STATUS"] != "OK":
                raise ValidationError(
                        f"SQL executable check failed. "
                        f"Fail log from DBMS: {res['RESULT']}"
                    )
            return True
        __output_format_check(response)
        if "sql" in response.keys(): __sql_executable_check(response, self.db_path)
        else: __response_history_compatible_check(response, history)
        return True
    
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
        
        if self.use_cache: return self._load_cached_test_cases()

        parser = get_parser(parser_name="nl_mutation_generation")
        history, outputs = [], []
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.max_retry:
                ret = Munch()
                ret.test_fixtures = Munch()
                # Generate nl mutants and using the original sql as fake prediction
                if not self.nl_mutants:
                    prompt = get_prompt(
                        template_name="nl_mutation_generation",
                        history_string=__history_to_string(history) if history else None
                    )
                    response = self.backbone(prompt, parser, request_kwargs={"HINT": self.hint, "QUESTION": self.nl})
                    try:
                        self._validate_test_fixture(response, history)# if any(nl_mutant == h.nl_mutant for h in history):
                    except ValidationError as e:
                        logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                        if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                        retry += 1
                        continue
                    # Temporarily save the generated NL mutants for future model ensemble use
                    data_to_add = [{"db_id": self.db_id, "question": response["nl"], "query": self.sql}]
                    if os.path.exists(self.nl_mutants_saved_path):
                        with open(self.nl_mutants_saved_path) as f:
                            try:
                                data = json.load(f)
                            except json.JSONDecodeError:
                                data = []
                    else:
                        data = []
                    data.extend(data_to_add)
                    with open(self.nl_mutants_saved_path, "w") as f: json.dump(data, f, indent=4)
                    response2 = {"sql": self.sql} # fake prediction
                    try:
                        self._validate_test_fixture(response2, history)
                    except ValidationError as e:
                        logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                        if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")    
                        retry += 1
                        continue
                else:
                    response = {"nl": self.nl_mutants[self.cnt]["question"]}
                    response2 = {"sql": self.nl_mutants_sql_outputs[self.cnt]}
                    self.cnt += 1
                ret.test_fixtures.nl_mutant = response["nl"]
                ret.test_fixtures.predict_sql = response2["sql"]
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class QueryReviewTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Step-through Query Review Test Class", "query_review", "explore", **kwargs)
        self.backbone = ModelFactory.create(
            model_platform=ModelPlatformType.AZURE,
            model_type=ModelType.GPT_4O_MINI,
            model_config_dict=ChatGPTConfig().as_dict() # [Optional] the config for model
        )        
        self.test_cases = self._generator()

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
    
    def _generator(self):
        if self.use_cache: return self._load_cached_test_cases()

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
            
            parsed_response = {
                "user_msg": user_response.msg.content,
                **json.loads(assistant_response.msg.content.strip())
            }
            if "CAMEL_TASK_DONE" in user_response.msg.content: break
            turns.append(parsed_response)
            input_msg = assistant_response.msg
    
        ret.test_fixtures.turns = turns
        outputs.append(self._form_instance(len(outputs), ret))

        return outputs

class NLReviewTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Step-through Natural Language Review Test Class", "nl_review", "explore", **kwargs)
        self.backbone = ModelFactory.create(
            model_platform=ModelPlatformType.AZURE,
            model_type=ModelType.GPT_4O_MINI,
            model_config_dict=ChatGPTConfig().as_dict() # [Optional] the config for model
        )
        self.test_cases = self._generator()

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
    
    def _generator(self):
        if self.use_cache: return self._load_cached_test_cases()

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

            # print_text_animated(Fore.BLUE + f"AI User:\\n\\n{user_response.msg.content}\\n" + Style.RESET_ALL)
            # print_text_animated(Fore.GREEN + f"AI Assistant:\\n\\n{assistant_response.msg.content}\\n" + Style.RESET_ALL)
            
            parsed_response = {
                "user_msg": user_response.msg.content,
                **json.loads(assistant_response.msg.content.strip())
            }
            turns.append(parsed_response)
            if "CAMEL_TASK_DONE" in user_response.msg.content: break
            
            input_msg = assistant_response.msg
        
        ret.test_fixtures.turns = turns
        outputs.append(self._form_instance(len(outputs), ret))
        
        return outputs