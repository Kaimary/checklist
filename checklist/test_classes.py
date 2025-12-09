import os, re, json, random, copy, logging
import time
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
        return passed, ret.test_fixtures, ret.results, None, 0
    
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
    def __init__(self, red_schema, **kwargs):
        super().__init__("Semantic Check Test Class", "semantic_check", "semantic", key="sql", **kwargs)

        self.schema = red_schema
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
        return passed, ret.test_fixtures, ret.results, None, 0
    
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
            # for b in bugs: print(f"level: {b.level}, desc: {b.description}")
            # Hard-code for spider to ignore `column type mismathes aggregation` bugs
            if "spider" in self.db_path: bugs = [bug for bug in bugs if not isinstance(bug, str) and "but function" not in bug.description]
            if bugs: 
                logging.info("\nBugs found:\n{}".format("\n".join(bug.description if not isinstance(bug, str) else bug for bug in bugs)))
            ret.test_fixtures.bugs = bugs
            outputs.append(self._form_instance(len(outputs), ret))
            del parsed_query

        return outputs

class OracleResultTestClass(TestClass):
    def __init__(self, red_schema, pruning_threshold=20, **kwargs):
        super().__init__("Oracle Result Test Class", "oracle_result", "oracle", key="nl", **kwargs)

        self.red_schema = red_schema
        self.schema = DatabaseManager(db_id=self.db_id, db_root_path=self.db_root_path).get_db_schema() # type: ignore
        # first check if any valid hard-constrained has to meet in the query (e.g., WHERE country='China')
        # If exists, prompt LLM to make sure required columns/values existed.
        self.matched_conditions, self.matched_keys = {}, {}
        try:
            # start=time.time()
            parsed_query = Query(self.sql, copy.deepcopy(self.red_schema))
            self.matched_conditions = parsed_query.check_conditions()
            self.matched_keys = parsed_query.check_keys()
            # end = time.time()
            # print(f"RED took {end - start:.2f} seconds.")
        except Exception as e:
            print(e)

        self.schema_pruned = False
        # Prune the `lenthy` schema first, to ensure the quality of generated data 
        if any(len(cols) > pruning_threshold for cols in self.schema.values()):
            logging.warning(f"Database {self.db_id} has tables with more than {pruning_threshold} columns. Truncating the schema before generation ...")
            retry = 0
            error = set() # Append the error messages to avoid endless llm loop
            parser = get_parser(parser_name="schema_pruning")
            while True and retry < self.max_retry:
                prompt = get_prompt(
                    template_name="schema_pruning",
                    columns_string=', '.join(self.matched_conditions.keys()) if self.matched_conditions else None,
                    keys_string=', '.join([f"{t}.{c}" for t, c in self.matched_keys.items()]) if self.matched_keys else None,
                    error_string='\n'.join(error) if error else None)
                response, _ = self.backbone(prompt, parser, request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl,
                    "DATABASE_SCHEMA": json.dumps(self.schema, indent=4)
                    }
                )
                try:
                    self._validate_pruned_schema(response)
                    self.schema = response
                    logging.info(f"Pruned schema: {json.dumps(response, indent=4)}")
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
        # self.max_retry = self.num * 1 # increase the max retry to 3 times of num for this test class
        start = time.time()
        self.test_cases = self._generator()
        end = time.time()
        logging.info(f"Generate tests took {end - start:.2f} seconds.")
        
    def _compare_query_results(self, preds, oracles):
        def __freeze(obj):
            """Recursively convert unhashable objects into hashable equivalents."""
            if isinstance(obj, dict):
                return tuple(sorted((k, __freeze(v)) for k, v in obj.items()))
            elif isinstance(obj, (list, tuple, set)):
                return tuple(__freeze(x) for x in obj)
            elif isinstance(obj, np.ndarray):
                return tuple(obj.tolist())
            else:
                return obj
        def __is_subset(pred, oracle_row):
            """Check if pred tuple is a subsequence of oracle_row tuple."""
            n, m = len(pred), len(oracle_row)
            if n > m:
                return False
            # 尝试匹配 pred 在 oracle_row 中的某个连续子序列
            for i in range(m - n + 1):
                if oracle_row[i:i+n] == pred:
                    return True
            return False

        if not preds or not oracles: return False

        preds_frozen = [__freeze(p) for p in preds]
        oracle_frozen = [__freeze(o) for o in oracles]

        # relax the comparision if one column matches, cuz `oracles` may include redunctant columns
        for p in preds_frozen:
            if not any(__is_subset(p, o) for o in oracle_frozen): return False
        return True
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        # Test the original SQL over a faked database with expected execution results
        res = validate_sql_query(ret.test_fixtures.db, self.sql, max_returned_rows="all")
        logging.info(f"Validating SQL: {self.sql}")
        ret.results.pred = res['RESULT'] if res['STATUS'] == 'OK' else None
        # the simulated database can't execute the sql propertly, most probably the simulation missing some pk/fk-like columns
        # to ensure good performance, set a special tag in the ret to make final detection as "UNDETERMINED"
        if not ret.results.pred: ret.results.orc_tag = True
        ret.results.target = ret.test_fixtures.label_result["rows"] if "rows" in ret.test_fixtures.label_result.keys() else []
        logging.info(f"Predicted Result: {ret.results.pred}, Target Result: {ret.results.target}")
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used
    
    def _validate_pruned_schema(self, response):
        def __extract_column_name(column_def):
            # Pattern to match: quoted strings or plain words
            pattern = r'''
                (["'])(.*?)\1 |  # Double/single quoted strings
                (`)(.*?)`      |  # Backtick quoted strings  
                (\w+)             # Plain words
            '''
            
            match = re.search(pattern, column_def, re.VERBOSE)
            if match:
                # Find which group actually matched
                if match.group(1):  # Double/single quotes
                    return match.group(2)
                elif match.group(3):  # Backticks
                    return match.group(4)
                elif match.group(5):  # Plain word
                    return match.group(5)
            return None

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
                    pk_column_name = __extract_column_name(column_def)
                    if pk_column_name not in response[table_name]:
                        # assuming the primary key column is the first column
                        response[table_name].insert(0, pk_column_name)
            return True                 

    def _validate_test_fixture(self, response, history, key="database_instances", instances=None):
        def __output_format_check(response, key):
            if not isinstance(response, dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"response type: {type(response)}, "
                    f"Expected type: dict"
                )
            # quick fix (hard-code) before checking
            if "columns" in response.keys() and "rows" in response.keys(): response = {"resulting_data": response}
            if key == "resulting_data": response["database_instances"] = instances
            if key not in response.keys():
                raise ValidationError(
                    f"Output format(key) check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `{key}`"
                )
            if key == "resulting_data" and any(k not in response["resulting_data"].keys() for k in ["columns", "rows"]): 
                raise ValidationError(
                    f"Output format(key in key) check failed. "
                    f"Keys found in `resulting_data`: {','.join(response['resulting_data'].keys())}, "
                    f"Expected keys: `columns` and `rows`"
                )
            return True
        def __extract_column_types_from_schema_string(schema_string):
            constraints = ('primary key', 'foreign key', 'unique', 'check', 'constraint')

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
                    # if 'foreign key' in column_def.lower(): continue
                    # 跳过表级约束
                    if column_def.lower().startswith(constraints): continue
                    match = type_regex.search(column_def)
                    if match:
                        types.append(match.group(1).upper())
                res[table_name] = types
            return res
        def __resulting_schema_check(response, schema_dict): # deprecated
            all_columns = []
            for columns in schema_dict.values():
                all_columns.extend(columns)

            if any(c not in all_columns for c in response["resulting_data"]["columns"]):
                raise ValidationError(
                    f"Resulting schema check failed. "
                    f"Resulting schema: {', '.join(response['resulting_data']['columns'])}"
                )
            return
        def __schema_data_alignment_check(response, tables, column_types, schema):
            def __normalize_sqlite_type(tp: str) -> str:
                """Normalize SQLite type (case-insensitive, strip length, etc.)."""
                tp = tp.upper().strip()
                # Remove size qualifiers, e.g., VARCHAR(20) -> VARCHAR
                tp = re.sub(r'\s*\(.*\)', '', tp)
                return tp
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
                'DATETIME': str,
                'bool': bool,
                "VARCHAR": str
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
                    # print(tp)
                    normalized = __normalize_sqlite_type(tp)
                    expected_type = sqlite_type_map.get(normalized, str)
                    try:
                        if v is not None:
                            expected_type(v)
                    except (ValueError, TypeError):
                        raise ValidationError(
                            f"Schema-data column type mismatch. "
                            f"Column type Data: {v} "
                            f"Expected column type: {expected_type}"
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
                    raise ValidationError("Duplicate(`database_instances`+`resulting_data`) test case.")
                # Otherwise, double check which `result` is the correct one
                retry = 0
                prompt = get_prompt(template_name="oracle_result_checking", schema_string=self.schema_string)
                parser = get_parser(parser_name="oracle_result_checking")
                while True and retry < self.max_retry:
                    response2, _ = self.backbone(prompt, parser, request_kwargs={
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
        __output_format_check(response, key)
        # __resulting_schema_check(response, self.schema if self.schema_pruned else DatabaseManager().get_db_schema())
        # schema-data alignment check
        if key == "database_instances":
            table_names = DatabaseManager().get_db_all_tables() if not self.schema_pruned else [k for k in self.schema.keys()]
            column_types= DatabaseManager().get_all_column_types() \
                if not self.schema_pruned else __extract_column_types_from_schema_string(self.schema_string)
            __schema_data_alignment_check(response, table_names, column_types, self.schema)
        # response duplication check
        else: __response_history_compatible_check(response, history)
       
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
            duplicate_sqlite_database(src_db_path=self.db_path, dest_db_path=ret.test_fixtures.db)
        else:
            create_sqlite_database(ret.test_fixtures.db, self.schema_string)
        for t, rows in ret.test_fixtures.data.items(): insert_rows_into_table(ret.test_fixtures.db, table_name=t, rows=rows)
        # # test case serialization
        # self.write_test_fixture_file(output_dir=TEST_INSTANCE_ROOT_PATH, 
        #     database=ret.test_fixtures.db, sql=self.sql, expect=ret.test_fixtures.label_result)
        
        return ret
    
    def _generator(self, verbose=True):
        def __history_to_string(history):
            return "\n".join(
                f"--- Example {i+1} ---\n"
                f"database_instances: {json.dumps(h['data'], indent=4)}\n\n"
                # f"resulting_data: {json.dumps(h['label_result'], indent=4)}"
                for i, h in enumerate(history)
            )
        def __values_to_string(col2vals):
            return "\n".join(
                f"Column `{col}`: {', '.join(vals)};"
                for col, vals in col2vals.items()
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
                tokens = 0
                start=time.time()
                prompt = get_prompt(
                    template_name="oracle_data_generation", 
                    schema_string=self.schema_string,
                    columns_values_string=__values_to_string(self.matched_conditions) if self.matched_conditions else None,
                    history_string=__history_to_string(history) if history else None
                )
                response, metadata = self.backbone(prompt, parser, request_kwargs={"QUESTION": self.nl, "HINT": self.hint})
                tokens += metadata.get("token_used", 0)
                end=time.time()
                logging.info(f"oracle_data_generation took {end - start:.2f} seconds.")
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
                start=time.time()
                response2, metadata2 = self.backbone(prompt2, parser2, request_kwargs={
                    "QUESTION": self.nl,
                    "HINT": self.hint,
                    "DATABASE_INSTANCES": json.dumps(response["database_instances"], indent=4)
                    }
                )
                tokens += metadata2.get("token_used", 0)
                end=time.time()
                logging.info(f"oracle_data_verification took {end - start:.2f} seconds.")
                try:
                    self._validate_test_fixture(response2, history, key="resulting_data", instances=response["database_instances"])
                except ValidationError as e:
                    logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                    if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                    retry += 1
                    continue
                # token usuage and logprobs
                ret.logprob = metadata2.get("logprob", None)
                ret.token_used = tokens
                ret.test_fixtures.data = response["database_instances"]
                ret.test_fixtures.label_result = response2["resulting_data"]
                # logging.info(f"Generated test fixture: \nChain-of-the-Thought: {response2['explanation']}\nDatabase Instances: {json.dumps(ret.test_fixtures.data, indent=4)}\nExpected Result: {json.dumps(ret.test_fixtures.label_result, indent=4)}")
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class NLRelaxTestClass(TestClass):
    def __init__(self, red_schema, **kwargs):
        super().__init__("Natural Language Relaxing Test Class", "nl_relax", "metamorphic", **kwargs)
        self.schema = red_schema
        self.test_cases = self._generator()

    def _compare_query_results(self, orgin, mutant):
        if orgin is None or mutant is None: return False
        return len(orgin) <= len(mutant)
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        # ret.description = "Test the original SQL over a faked database with expected execution results"
        ret.results.pred = execute_sql(self.db_path, ret.test_fixtures.sql_mutant)
        res = validate_sql_query(self.db_path, self.sql, max_returned_rows="all")
        ret.results.target = res["RESULT"] if res["STATUS"] == "OK" else None
        if len(ret.results.pred) < 10: logging.info(f"Predicted Result: {ret.results.pred}, Target Result: {ret.results.target}")
        ret.results.standard = "len(pred) >= len(target)"
        passed = self._compare_query_results(ret.results.target, ret.results.pred)
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used
    
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
        # check query clauses and skip the test if constraint-relatd clauses (WHERE/ORDER/GROUP/IUE) are missing
        clauses = []
        try:
            parsed_query = Query(self.sql, copy.deepcopy(self.schema))
            clauses = list(parsed_query.clauses.keys())
        except Exception as e:
            print(e)
        if not clauses or all(c not in clauses for c in [
            "WHERE", "LIMIT", "HAVING", "INTERSECT", "INTERSECT ALL", "UNION", "UNION ALL", "EXCEPT", "EXCEPT ALL"]
        ): return outputs
        
        invalids = set()
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num and retry < self.max_retry:
                ret = Munch()
                ret.test_fixtures = Munch()
                tokens = 0
                prompt = get_prompt(
                    template_name="nl_relaxing_generation", 
                    invalid_queries_string=__error_to_string(invalids) if invalids else None,
                    history_string=__history_to_string(history) if history else None
                )
                response, metadata = self.backbone(prompt, parser, 
                    request_kwargs={
                        "HINT": self.hint,
                        "QUESTION": self.nl,
                        "QUERY": self.sql
                    }
                )
                tokens += metadata.get("token_used", 0)
                # no constraint found, skip directly
                if isinstance(response, dict) and 'type' in response.keys() and response['type'] == 'unknown': break
                try:
                    self._validate_test_fixture(response, history)
                except ValidationError as e:
                    retry += 1
                    logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                    if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                    if "sql_mutant" in response.keys(): invalids.add((response["sql_mutant"], str(e)))
                    continue
                
                ret.token_used = tokens
                ret.logprob = metadata.get("logprob", None)
                ret.type = response["type"]
                ret.desc = response["description"]
                ret.test_fixtures.nl_mutant = response["nl_mutant"]
                ret.test_fixtures.sql_mutant = response["sql_mutant"] 
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                logging.info(f"Generated test fixtures:\n{json.dumps(response, indent=4)}")
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class NLStrengthenTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Natural Language Strengthening Test Class", "nl_strengthen", "metamorphic", **kwargs)
        self.test_cases = self._generator()

    def _compare_query_results(self, orgin, mutant):
        if orgin is None or mutant is None: return False
        return len(orgin) >= len(mutant)
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = execute_sql(self.db_path, ret.test_fixtures.sql_mutant)
        res = validate_sql_query(self.db_path, self.sql, max_returned_rows="all")
        ret.results.target = res["RESULT"] if res["STATUS"] == "OK" else None
        if ret.results.target and len(ret.results.target) < 10: logging.info(f"Predicted Result: {ret.results.pred}, Target Result: {ret.results.target}")
        ret.results.standard = "len(pred) <= len(target)"
        passed = self._compare_query_results(ret.results.target, ret.results.pred)
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used
    
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
                f"sql mutation: {sql}\n\n"
                for sql in invalids
            )
        
        if self.use_cache: return self._load_cached_test_cases()

        parser = get_parser(parser_name="nl_strengthening_generation")
        history, outputs = [], []
        tokens = 0
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
                response, metadata = self.backbone(prompt, parser, 
                    request_kwargs={
                        "HINT": self.hint,
                        "QUESTION": self.nl,
                        "QUERY": self.sql
                    }
                )
                tokens += metadata.get("token_used", 0)
                # no constraint found, skip directly
                if isinstance(response, dict) and 'type' in response.keys() and response['type'] == 'unknown': break
                try:
                    self._validate_test_fixture(response, history)
                except ValidationError as e:
                    retry += 1
                    logging.warning(f"Test fixture validation failed (attempt {retry}/{self.max_retry}): {e}")
                    if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                    if isinstance(response, dict) and "sql_mutant" in response.keys(): invalids.add(response["sql_mutant"])
                    continue
                # token usuage and logprobs
                ret.token_used = tokens
                ret.logprob = metadata.get("logprob", None)
                ret.type = response["type"]
                ret.desc = response["description"]
                ret.test_fixtures.nl_mutant = response["nl_mutant"]
                ret.test_fixtures.sql_mutant = response["sql_mutant"]
                logging.info(f"Generated test fixtures:\n{json.dumps(response, indent=4)}")
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
            "omnisql32b": OMNISQL32b,
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
        return passed, ret.test_fixtures, ret.results, None, 0
    
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
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used
    
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
                tokens = 0
                # Generate nl mutants and using the original sql as fake prediction
                if not self.nl_mutants:
                    prompt = get_prompt(
                        template_name="nl_mutation_generation",
                        history_string=__history_to_string(history) if history else None
                    )
                    response, metadata = self.backbone(prompt, parser, request_kwargs={"HINT": self.hint, "QUESTION": self.nl})
                    tokens += metadata.get("token_used", 0)
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
                ret.token_used = tokens
                ret.logprob = metadata.get("logprob", None)
                ret.test_fixtures.nl_mutant = response["nl"]
                ret.test_fixtures.predict_sql = response2["sql"]
                history.append(ret.test_fixtures)
                outputs.append(self._form_instance(len(outputs), ret))
                spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return outputs

class QueryReviewTestClass(TestClass):
    def __init__(self, red_schema, **kwargs):
        super().__init__("Step-through Query Review Test Class", "query_review", "explore", **kwargs)
        self.schema = red_schema
        self.backbone = ModelFactory.create(
            model_platform=ModelPlatformType.AZURE,
            model_type=ModelType.GPT_4O_MINI if self.backbone == "gpt-4o-mini-0708" else ModelType.GPT_5_1,
            model_config_dict=ChatGPTConfig(temperature=0).as_dict() # [Optional] the config for model
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
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used
        
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

        # Obtain query clauses for next debugging purpose
        clauses = []
        try:
            parsed_query = Query(self.sql, copy.deepcopy(self.schema))
            clauses = list(parsed_query.clauses.keys())
        except Exception as e:
            print(e)
        
        outputs = []
        ret = Munch()
        ret.test_fixtures = Munch()
        prompt = get_prompt(template_name="query_rubber_duck_debugging", schema_string=self.schema_string)
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num:
                random.shuffle(clauses)
                task_prompt = prompt.invoke({
                    "QUESTION": self.nl,
                    "HINT": self.hint,
                    "SQL": self.sql,
                    "CLAUSES": "- " + ", ".join(clauses) if clauses else "",
                    "RANDOMNESS1": str(random.randint(3, 6)),
                    "RANDOMNESS2": str(random.randint(15, 40))
                    }
                ).messages[0].content
                role_play_session = RolePlaying(
                    assistant_role_name="SQL Developer",
                    assistant_agent_kwargs=dict(model=self.backbone),
                    user_role_name="Rubber Duck Debugging Assistant",
                    user_agent_kwargs=dict(model=self.backbone),
                    task_prompt=task_prompt,
                    with_task_specify=False
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
                tokens = 0
                # Turn-based simulation
                while n < chat_turn_limit:
                    n += 1
                    assistant_response, user_response = role_play_session.step(input_msg)
                    tokens += assistant_response.info.get("usage")["total_tokens"] + user_response.info.get("usage")["total_tokens"]
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

                ret.token_used = tokens
                ret.logprob = None
                ret.test_fixtures.turns = turns
                outputs.append(self._form_instance(len(outputs), ret))

        return outputs

class NLReviewTestClass(TestClass):
    def __init__(self, **kwargs):
        super().__init__("Step-through Natural Language Review Test Class", "nl_review", "explore", **kwargs)
        self.backbone = ModelFactory.create(
            model_platform=ModelPlatformType.AZURE,
            model_type=ModelType.GPT_4O_MINI if self.backbone == "gpt-4o-mini-0708" else ModelType.GPT_5_1,
            model_config_dict=ChatGPTConfig(temperature=0).as_dict() # [Optional] the config for model
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
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used
        
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
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num:
                task_prompt = prompt.invoke({
                    "QUESTION": self.nl,
                    "HINT": self.hint,
                    "SQL": self.sql,
                    "RANDOMNESS1": str(random.randint(2, 5)),
                    "RANDOMNESS2": str(random.randint(15, 40))
                    }
                ).messages[0].content
                role_play_session = RolePlaying(
                    assistant_role_name="SQL Developer",
                    assistant_agent_kwargs=dict(model=self.backbone),
                    user_role_name="Rubber Duck Debugging Assistant",
                    user_agent_kwargs=dict(model=self.backbone),
                    task_prompt=task_prompt,
                    with_task_specify=False
                )
                n = 0
                chat_turn_limit = 10
                turns = []
                input_msg = role_play_session.init_chat()
                tokens = 0
                # Turn-based simulation
                while n < chat_turn_limit:
                    n += 1
                    assistant_response, user_response = role_play_session.step(input_msg)
                    tokens += assistant_response.info.get("usage")["total_tokens"] + user_response.info.get("usage")["total_tokens"]
                    if assistant_response.terminated:
                        print(Fore.GREEN + f"AI Assistant terminated. Reason: {assistant_response.info['termination_reasons']}." + Style.RESET_ALL)
                        break
                    if user_response.terminated:
                        print(Fore.GREEN + f"AI User terminated. Reason: {user_response.info['termination_reasons']}." + Style.RESET_ALL)
                        break
                    
                    print(f"\033[94mAI User:\n\n{user_response.msg.content}\033[0m", flush=True)
                    print(f"\033[92mAI Assistant:\n\n{assistant_response.msg.content}\033[0m", flush=True)
                    # print_text_animated(Fore.BLUE + f"AI User:\\n\\n{user_response.msg.content}\\n" + Style.RESET_ALL)
                    # print_text_animated(Fore.GREEN + f"AI Assistant:\\n\\n{assistant_response.msg.content}\\n" + Style.RESET_ALL)
                    
                    parsed_response = {
                        "user_msg": user_response.msg.content,
                        **json.loads(assistant_response.msg.content.strip())
                    }
                    turns.append(parsed_response)
                    if "CAMEL_TASK_DONE" in user_response.msg.content: break
                    
                    input_msg = assistant_response.msg
                
                ret.token_used = tokens
                ret.logprob = None
                ret.test_fixtures.turns = turns
                outputs.append(self._form_instance(len(outputs), ret))
        
        return outputs