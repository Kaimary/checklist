import os, re, time, json, random, copy, logging, threading, shutil, sqlite3
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import numpy as np
from munch import Munch

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

class SchemaPruningMixin:
    """Shared helpers for classes that optionally prune large schemas via LLM."""

    def _quote_identifier(self, identifier: str) -> str:
        escaped = identifier.replace('"', '""')
        return f'"{escaped}"'

    def _validate_pruned_schema(self, response):
        def __extract_column_name(column_def):
            pattern = r'''
                (["'])(.*?)\1 |  # Double/single quoted strings
                (`)(.*?)`      |  # Backtick quoted strings  
                (\w+)             # Plain words
            '''
            match = re.search(pattern, column_def, re.VERBOSE)
            if match:
                if match.group(1):
                    return match.group(2)
                if match.group(3):
                    return match.group(4)
                if match.group(5):
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
            for column_def in definitions:
                column_def = column_def.strip()
                if "primary key" in column_def.lower():
                    pk_column_name = __extract_column_name(column_def)
                    if pk_column_name not in response[table_name]:
                        response[table_name].insert(0, pk_column_name)
            return True

    def _copy_rows_into_pruned_db(self, target_db_path, schema_subset):
        dest_conn = sqlite3.connect(target_db_path)
        src_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            dest_cur = dest_conn.cursor()
            src_cur = src_conn.cursor()
            for table, columns in schema_subset.items():
                if not columns:
                    continue
                quoted_table = self._quote_identifier(table)
                quoted_columns = ', '.join(self._quote_identifier(col) for col in columns)
                select_sql = f"SELECT {quoted_columns} FROM {quoted_table}"
                try:
                    src_cur.execute(select_sql)
                except sqlite3.Error as exc:
                    logging.warning(
                        f"Skipping data backfill for table `{table}` during pruned DB build: {exc}"
                    )
                    continue
                placeholders = ', '.join('?' for _ in columns)
                insert_sql = f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})"
                while True:
                    rows = src_cur.fetchmany(512)
                    if not rows:
                        break
                    try:
                        dest_cur.executemany(insert_sql, rows)
                    except sqlite3.Error as exc:
                        logging.warning(
                            f"Failed inserting rows into table `{table}` for pruned DB build: {exc}"
                        )
                        break
            dest_conn.commit()
        finally:
            src_conn.close()
            dest_conn.close()

    def _prune_schema_if_needed(
        self,
        schema,
        pruning_threshold,
        matched_conditions=None,
        matched_keys=None,
    ):
        self.schema_pruned = False
        self._pruned_db_build_event = None
        self._pruned_db_build_error = None
        self._pruned_db_snapshot_path = None
        if pruning_threshold is None:
            return schema, False
        if not any(len(cols) > pruning_threshold for cols in schema.values()):
            return schema, False

        logging.warning(
            f"Database {self.db_id} has tables with more than {pruning_threshold} columns. "
            "Truncating the schema before generation ..."
        )
        retry = 0
        error = set()
        parser = get_parser(parser_name="schema_pruning")
        matched_conditions = matched_conditions or {}
        matched_keys = matched_keys or {}

        while retry < self.max_retry:
            prompt = get_prompt(
                template_name="schema_pruning",
                columns_string=', '.join(matched_conditions.keys()) if matched_conditions else None,
                keys_string=', '.join([f"{t}.{c}" for t, c in matched_keys.items()]) if matched_keys else None,
                error_string='\n'.join(error) if error else None
            )
            response, _ = self.backbone(
                prompt,
                parser,
                request_kwargs={
                    "HINT": self.hint,
                    "QUESTION": self.nl,
                    "DATABASE_SCHEMA": json.dumps(schema, indent=4)
                }
            )
            try:
                self._validate_pruned_schema(response)
                logging.info(f"Pruned schema: {json.dumps(response, indent=4)}")
                self.schema_pruned = True
                return response, True
            except ValidationError as e:
                error.add(str(e).split('.')[-1])
                retry += 1
                logging.warning(f"Pruned schema validation failed: {e}. Retrying...")

        return schema, False

    def _schedule_pruned_db_materialization(self, schema_string, copy_existing_rows=False):
        if not getattr(self, "schema_pruned", False):
            return
        if not schema_string:
            return
        if getattr(self, "_pruned_db_build_event", None):
            return
        self._pruned_db_snapshot_path = os.path.join(
            self.instance_saved_path,
            f"{self.db_id}_pruned_base.sqlite"
        )
        schema_subset = copy.deepcopy(self.schema)
        event = threading.Event()
        self._pruned_db_build_event = event
        self._pruned_db_build_error = None

        def _worker():
            try:
                os.makedirs(os.path.dirname(self._pruned_db_snapshot_path), exist_ok=True)
                create_sqlite_database(self._pruned_db_snapshot_path, schema_string)
                if copy_existing_rows:
                    self._copy_rows_into_pruned_db(self._pruned_db_snapshot_path, schema_subset)
            except Exception as exc:
                self._pruned_db_build_error = exc
                logging.exception("Failed to build pruned schema database snapshot", exc_info=exc)
            finally:
                event.set()

        threading.Thread(target=_worker, daemon=True).start()

    def _ensure_pruned_db_snapshot_ready(self):
        event = getattr(self, "_pruned_db_build_event", None)
        if not event:
            return None
        event.wait()
        if getattr(self, "_pruned_db_build_error", None):
            logging.warning(
                f"Pruned schema snapshot creation failed: {self._pruned_db_build_error}"
            )
            return None
        return getattr(self, "_pruned_db_snapshot_path", None)

class MinimumSyntaxTestClass(TestClass):
    def __init__(self):
        super().__init__("Minimum Syntax Test Class", "minimum_syntax", "syntax", key="sql")
    
    def set(self, **kwargs):
        super().set(**kwargs)
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
    def __init__(self):
        super().__init__("Semantic Check Test Class", "semantic_check", "semantic", key="sql")

    def set(self, red_schema, **kwargs):
        super().set(**kwargs)
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

class OracleResultTestClass(SchemaPruningMixin, TestClass):
    def __init__(self):
        super().__init__("Oracle Result Test Class", "oracle_result", "oracle", key="nl")

    def set(self, red_schema, pruning_threshold=20, **kwargs):
        super().set(**kwargs)
        self.num=3
        self.criteria=0.6
        self.parallel_workers = 3
        self.parser = get_parser(parser_name="simulate_db_generation")
        self.parser2 = get_parser(parser_name="oracle_data_generation")
        self.red_schema = red_schema
        self.schema = DatabaseManager(db_id=self.db_id, db_root_path=self.db_root_path).get_db_schema() # type: ignore
        # first check if any valid hard-constrained has to meet in the query (e.g., WHERE country='China')
        # If exists, prompt LLM to make sure required columns/values existed.
        self.matched_conditions, self.matched_keys = {}, {}
        try:
            parsed_query = Query(self.sql, copy.deepcopy(self.red_schema))
            self.matched_conditions = parsed_query.check_conditions()
            self.matched_keys = parsed_query.check_keys()
        except Exception as e:
            print(e)

        self.schema, self.schema_pruned = self._prune_schema_if_needed(
            schema=self.schema,
            pruning_threshold=pruning_threshold,
            matched_conditions=self.matched_conditions,
            matched_keys=self.matched_keys
        )
            
        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=self.schema,
            schema_with_examples=None, # type: ignore
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self._schedule_pruned_db_materialization(self.schema_string)
        start = time.time()
        self.test_cases = self._generator()
        logging.info(f"Generate tests took {time.time() - start:.2f} seconds.")   

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
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used, ret.trace

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
                    elif v1 != v2: return False
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
            snapshot_path = self._ensure_pruned_db_snapshot_ready()
            if snapshot_path and os.path.exists(snapshot_path):
                shutil.copy2(snapshot_path, ret.test_fixtures.db)
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
                for i, h in enumerate(history)
            )
        def __values_to_string(col2vals):
            return "\n".join(
                f"Column `{col}`: {', '.join(vals)};"
                for col, vals in col2vals.items()
            )

        history, outputs = [], []
        retry = 0
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        state_lock = threading.Lock()
        max_workers = max(1, min(getattr(self, "parallel_workers", 1), self.num))

        def _generate_candidate(history_string):
            ret = Munch()
            ret.test_fixtures = Munch()
            tokens, logprob = 0, 0
            trace = "->>Parallel Test Case Tracelog<<-\n"
            prompt = get_prompt(
                template_name="simulate_db_generation",
                schema_string=self.schema_string,
                columns_values_string=__values_to_string(self.matched_conditions) if self.matched_conditions else None,
                history_string=history_string
            )
            # start = time.time()
            response, metadata = self.backbone(
                prompt,
                self.parser,
                request_kwargs={"QUESTION": self.nl, "HINT": self.hint}
            )
            metadata = metadata or {}
            tokens += metadata.get("token_used", 0)
            logprob += metadata.get("logprob", None)
            trace += f"[simulated DB]: {response.get('database_instances', '')}"
            # logging.info(f"simulate_db_generation took {time.time() - start:.2f} seconds.")

            prompt2 = get_prompt(template_name="oracle_data_generation", schema_string=self.schema_string)
            response2, metadata2 = self.backbone(
                prompt2,
                self.parser2,
                request_kwargs={
                    "QUESTION": self.nl,
                    "HINT": self.hint,
                    "DATABASE_INSTANCES": json.dumps(response.get("database_instances", {}), indent=4)
                }
            )
            metadata2 = metadata2 or {}
            tokens += metadata2.get("token_used", 0)
            logprob += metadata2.get("logprob", None)
            ret.logprob = logprob*0.5
            ret.token_used = tokens
            trace += f"[oracle data]: {response2.get('resulting_data', '')}"
            ret.trace = trace
            ret.test_fixtures.data = response.get("database_instances", {})
            ret.test_fixtures.label_result = response2.get("resulting_data", {})
            
            return response, response2, ret

        def submit_task(executor, futures):
            with state_lock:
                if len(outputs) >= self.num or retry >= self.max_retry:
                    return False
                history_string = __history_to_string(history) if history else None
            future = executor.submit(_generate_candidate, history_string)
            futures.add(future)
            return True

        with spinner and ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = set()
            for _ in range(max_workers):
                if not submit_task(executor, futures):
                    break

            stop_generation = False
            while futures and not stop_generation:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done:
                    futures.remove(fut)
                    try:
                        response, response2, ret = fut.result()
                    except Exception as exc:
                        logging.exception("Oracle result generation worker failed", exc_info=exc)
                        with state_lock:
                            retry += 1
                            if verbose:
                                spinner.set_message(f"Test fixture generation failed (attempt {retry}/{self.max_retry})...")
                            stop_generation = len(outputs) >= self.num or retry >= self.max_retry
                        continue

                    appended_to_history = False
                    try:
                        with state_lock:
                            self._validate_test_fixture(response, history)
                            self._validate_test_fixture(
                                response2,
                                history,
                                key="resulting_data",
                                instances=response.get("database_instances")
                            )
                            history.append(ret.test_fixtures)
                            appended_to_history = True
                            outputs.append(self._form_instance(len(outputs), ret))
                            spinner.set_message(f"Generated {len(outputs)} test cases ...")
                            stop_generation = len(outputs) >= self.num or retry >= self.max_retry
                            if stop_generation:
                                break
                    except ValidationError as e:
                        with state_lock:
                            if appended_to_history and history:
                                history.pop()
                            retry += 1
                            if verbose:
                                spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                            stop_generation = len(outputs) >= self.num or retry >= self.max_retry
                        logging.warning(f"Test fixture validation failed: {e}")
                    except Exception as err:
                        with state_lock:
                            if appended_to_history and history:
                                history.pop()
                            retry += 1
                            if verbose:
                                spinner.set_message(f"Test fixture materialization failed (attempt {retry}/{self.max_retry})...")
                            stop_generation = len(outputs) >= self.num or retry >= self.max_retry
                        logging.exception("Failed to materialize oracle test instance", exc_info=err)

                    if stop_generation:
                        break

                    submit_task(executor, futures)

            for fut in futures:
                fut.cancel()

        return outputs

class NoiseRowTestClass(SchemaPruningMixin, TestClass):
    def __init__(self):
        super().__init__("Noise Row Injection Test Class", "metamorphic_noise", "metamorphic")

    def set(self, red_schema, pruning_threshold=20, **kwargs):
        super().set(**kwargs)
        self.red_schema = red_schema
        self.criteria=0.6
        self.num=3
        self.max_retry=3
        self.parallel_workers = 3
        self.parser = get_parser(parser_name="noise_data_injection")
        self.schema = DatabaseManager(db_id=self.db_id, db_root_path=self.db_root_path).get_db_schema() # type: ignore
        matched_conditions, matched_keys = {}, {}
        try:
            parsed_query = Query(self.sql, copy.deepcopy(self.red_schema))
            matched_conditions = parsed_query.check_conditions()
            matched_keys = parsed_query.check_keys()
        except Exception as e:
            print(e)
        self.schema, self.schema_pruned = self._prune_schema_if_needed(
            schema=self.schema,
            pruning_threshold=pruning_threshold,
            matched_conditions=matched_conditions,
            matched_keys=matched_keys
        )
        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=self.schema,
            schema_with_examples=None, # type: ignore
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )
        self._schedule_pruned_db_materialization(self.schema_string, copy_existing_rows=True)
        self.test_cases = self._generator()

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
        # Test the original SQL over the noise-injected database with expected execution results
        res = validate_sql_query(ret.test_fixtures.db, self.sql, max_returned_rows="all")
        logging.info(f"Validating SQL: {self.sql}")
        ret.results.pred = res['RESULT'] if res['STATUS'] == 'OK' else None
        res = validate_sql_query(self.db_path, self.sql, max_returned_rows="all")
        ret.results.target = res['RESULT'] if res['STATUS'] == 'OK' else None
        logging.info(f"Predicted Result: {ret.results.pred}, Target Result: {ret.results.target}")
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used, ret.trace
    
    def _validate_test_fixture(self, response, history):
        def __output_format_check(response):
            if not isinstance(response, dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"response type: {type(response)}, "
                    f"Expected type: dict"
                )
            if "injected_rows" not in response.keys():
                raise ValidationError(
                    f"Output format(key) check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `injected_rows`"
                )
            return True
        def __schema_data_alignment_check(response, tables, column_types, schema):
            def __normalize_sqlite_type(tp: str) -> str:
                """Normalize SQLite type (case-insensitive, strip length, etc.)."""
                tp = tp.upper().strip()
                # Remove size qualifiers, e.g., VARCHAR(20) -> VARCHAR
                tp = re.sub(r'\s*\(.*\)', '', tp)
                return tp
            # table name validity check
            tables_in_data = response["injected_rows"].keys()
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
            data = response["injected_rows"]
            for t, row in data.items():
                if not row: continue
                # hard-code to convert ``incorrect'' nested list into list for parsing
                if isinstance(row[0], list): 
                    row = row[0]
                    data[t] = row
                expected_len = len(column_types[t])
                if expected_len != len(row):
                    fixed_row = None
                    # len_delta = abs(expected_len - len(row))
                    # if len_delta <= 1:
                    #     fixed_row = self._attempt_row_alignment_fix(
                    #         table_name=t,
                    #         row=row,
                    #         column_names=schema.get(t, []),
                    #         column_types=column_types[t]
                    #     )
                    if fixed_row is None:
                        raise ValidationError(
                            f"Schema-data column count mismatch. "
                            f"Column count of table `{t}` in data row: {len(row)} (e.g., {row}), "
                            f"Expected column count: {expected_len}({','.join(schema[t])})"
                        )
                    # row = fixed_row
                    # data[t] = row
                
                for v, tp in zip(row, column_types[t]):
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
        def __response_history_compatible_check(response, history):
            def __dicts_equal___(d1, d2):
                if d1.keys() != d2.keys():
                    return False
                
                for key in d1:
                    v1, v2 = d1[key], d2[key]
                    # If both are lists, check order-insensitive equality
                    if isinstance(v1, list) and isinstance(v2, list) and set(v1) != set(v2): return False
                return True
            
            for h in history:
                if __dicts_equal___(response["injected_rows"], h["data"]):
                    raise ValidationError("Duplicate(`injected_rows`) test case.")
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

    def _attempt_row_alignment_fix(self, table_name, row, column_names, column_types):
        """
        Attempt to repair a minor column count mismatch by asking the backbone LLM
        to produce an aligned row that matches the table schema.
        """
        if not getattr(self, "repair_parser", None):
            return None
        if not column_names or not column_types:
            return None
        column_spec_payload = []
        for idx, name in enumerate(column_names):
            column_spec_payload.append({
                "name": name,
                "type": column_types[idx] if idx < len(column_types) else "TEXT"
            })
        issue_description = (
            f"Table `{table_name}` expects {len(column_names)} columns but received {len(row)}."
        )
        prompt = get_prompt(template_name="noise_data_alignment_fix")
        response, _ = self.backbone(
            prompt,
            self.repair_parser,
            request_kwargs={
                "HINT": self.hint,
                "QUESTION": self.nl,
                "TABLE_NAME": table_name,
                "COLUMN_SPEC": json.dumps(column_spec_payload, ensure_ascii=False, indent=2),
                "ROW_VALUES": json.dumps(list(row), ensure_ascii=False),
                "ISSUE_DESCRIPTION": issue_description
            }
        )
        fixed_rows = response.get("fixed_rows") if isinstance(response, dict) else None
        if not isinstance(fixed_rows, dict): return None
        candidate = fixed_rows.get(table_name)
        if candidate is None and fixed_rows:
            candidate = next(iter(fixed_rows.values()))
        if candidate is None:
            return None
        if len(candidate) != len(column_names):
            logging.warning(
                f"Row auto-fix produced {len(candidate)} values for `{table_name}`, "
                f"but {len(column_names)} are required."
            )
            return None
        logging.info(f"Auto-fixed row for table `{table_name}`: {row} -> {candidate}")
        return candidate
        
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
            duplicate_sqlite_database(src_db_path=self.db_path, dest_db_path=ret.test_fixtures.db, reset=False)
        else:
            snapshot_path = self._ensure_pruned_db_snapshot_ready()
            if snapshot_path and os.path.exists(snapshot_path):
                shutil.copy2(snapshot_path, ret.test_fixtures.db)
            else:
                logging.warning("Pruned schema snapshot unavailable; rebuilding synchronously for NoiseRowTestClass.")
                create_sqlite_database(ret.test_fixtures.db, self.schema_string)
                self._copy_rows_into_pruned_db(ret.test_fixtures.db, self.schema)
        for t, row in ret.test_fixtures.data.items(): insert_rows_into_table(ret.test_fixtures.db, table_name=t, rows=[row])
        
        return ret
    
    def _generator(self, verbose=True):
        def __history_to_string(history):
            return "\n".join(
                f"--- Example {i+1} ---\n"
                f"injected rows: {json.dumps(h['data'], indent=4)}\n\n"
                for i, h in enumerate(history)
            )

        retry = 0
        history, outputs = [], []
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        state_lock = threading.Lock()
        max_workers = max(1, min(self.parallel_workers, self.num))

        def _generate_candidate(history_string):
            ret = Munch()
            ret.test_fixtures = Munch()
            trace = f"->>Parallel Test Case Tracelog<<-\n"

            prompt = get_prompt(
                template_name="noise_data_injection",
                schema_string=self.schema_string,
                history_string=history_string
            )
            response, metadata = self.backbone(prompt, self.parser, request_kwargs={"QUESTION": self.nl, "HINT": self.hint})
            metadata = metadata or {}
            trace += f"[injected rows]: {response.get('injected_rows', '')}"
            ret.token_used = metadata.get("token_used", 0)
            ret.logprob = metadata.get("logprob", None)
            ret.trace = trace
            ret.test_fixtures.data = response.get("injected_rows", {})
            return response, ret

        def submit_task(executor, futures):
            with state_lock:
                if len(outputs) >= self.num or retry >= self.max_retry: return False
                history_string = __history_to_string(history) if history else None
            future = executor.submit(_generate_candidate, history_string)
            futures.add(future)
            return True

        with spinner and ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = set()
            for _ in range(max_workers):
                if not submit_task(executor, futures):
                    break

            stop_generation = False
            while futures and not stop_generation:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done:
                    futures.remove(fut)
                    try:
                        response, ret = fut.result()
                    except Exception as exc:
                        logging.exception("Noise data generation worker failed", exc_info=exc)
                        with state_lock:
                            retry += 1
                            if verbose:
                                spinner.set_message(f"Test fixture generation failed (attempt {retry}/{self.max_retry})...")
                        continue

                    appended_to_history = False
                    try:
                        with state_lock:
                            self._validate_test_fixture(response, history)
                            if ret.test_fixtures.data is None:
                                raise ValidationError("Missing `injected_rows` in response.")
                            history.append(ret.test_fixtures)
                            appended_to_history = True
                            outputs.append(self._form_instance(len(outputs), ret))
                            spinner.set_message(f"Generated {len(outputs)} test cases ...")
                            stop_generation = len(outputs) >= self.num or retry >= self.max_retry
                            if stop_generation: break
                    except ValidationError as e:
                        with state_lock:
                            if appended_to_history and history:
                                history.pop()
                            retry += 1
                            if verbose:
                                spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                        logging.warning(f"Test fixture validation failed: {e}")
                    except Exception as err:
                        with state_lock:
                            if appended_to_history and history:
                                history.pop()
                            retry += 1
                            if verbose:
                                spinner.set_message(f"Test fixture materialization failed (attempt {retry}/{self.max_retry})...")
                        logging.exception("Failed to materialize test instance", exc_info=err)

                    with state_lock:
                        stop_generation = len(outputs) >= self.num or retry >= self.max_retry

                    if stop_generation:
                        break

                    submit_task(executor, futures)

            for fut in futures:
                fut.cancel()

        return outputs
    
class CrossModelTestClass(TestClass):
    def __init__(self):
        super().__init__("Majority Voting Test Class", "majority_vote", "differential")
        
    def set(self, **kwargs):
        super().set(**kwargs)
        self.num=3
        self.active_model_num = 3
        model_list=(["resdsql", "codes15b", "dailsql", "llm:deepseek-chat"] if "spider" in self.db_root_path else \
                     ["chess", "cscsql32b", "omnisql32b", "llm:deepseek-chat"])
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
    def __init__(self):
        super().__init__("Query Consistency Test Class", "query_consistency", "differential")
    
    def set(self, **kwargs):
        super().set(**kwargs)
        self.num=3
        self.criteria=0.3
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
    def __init__(self):
        super().__init__("Step-through Query Review Test Class", "query_review", "explore")

    def set(self, red_schema, **kwargs):
        super().set(**kwargs)
        self.schema = red_schema
        self.test_cases = self._generator()

    def _compare_query_results(self, preds, targets):
        for pred, target in zip(preds, targets):
            if pred != target: return False
        return True
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = [f"{turn['judgment']}" for turn in ret.test_fixtures.turns]
        ret.results.target = ['True' for _ in ret.test_fixtures.turns]
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used, ret.trace
        
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
        parser = get_parser(parser_name="query_rubber_duck_debugging")
        prompt = get_prompt(template_name="query_rubber_duck_debugging", schema_string=self.schema_string)
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num:
                tokens = 0
                trace = f"->>Test Case {len(outputs)+1} Tracelog<<-\n"
                random.shuffle(clauses)
                response, metadata = self.backbone(prompt, parser, request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl,
                    "SQL": self.sql,
                    "CLAUSES": clauses
                    }
                )
                trace += f"{response['chain_of_thought_reasoning']}\n"
                trace += f"{response['judgment']}"
                tokens += metadata.get("token_used", 0)

                ret.token_used = tokens
                ret.logprob = None
                ret.test_fixtures.turns = [response]
                ret.trace = trace
                outputs.append(self._form_instance(len(outputs), ret))

        return outputs

class NLReviewTestClass(TestClass):
    def __init__(self):
        super().__init__("Step-through Natural Language Review Test Class", "nl_review", "explore")

    def set(self, **kwargs):
        super().set(**kwargs)
        self.test_cases = self._generator()

    def _compare_query_results(self, preds, targets):
        for pred, target in zip(preds, targets):
            if pred != target: return False
        return True
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = [f"{turn['judgment']}" for turn in ret.test_fixtures.turns]
        ret.results.target = ['True' for _ in ret.test_fixtures.turns]
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target)
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used, ret.trace
        
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
        outputs = []
        ret = Munch()
        ret.test_fixtures = Munch()
        parser = get_parser(parser_name="nl_rubber_duck_debugging")
        prompt = get_prompt(template_name="nl_rubber_duck_debugging", schema_string=self.schema_string)
        spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        with spinner:
            while len(outputs) < self.num:
                tokens = 0
                trace = f"->>Test Case {len(outputs)+1} Tracelog<<-\n"                
                response, metadata = self.backbone(prompt, parser, request_kwargs={
                    "HINT": self.hint, 
                    "QUESTION": self.nl,
                    "SQL": self.sql
                    }
                )
                trace += f"{response['chain_of_thought_reasoning']}\n"
                trace += f"{response['judgment']}"
                tokens += metadata.get("token_used", 0)
                
                # token usuage and logprobs
                ret.logprob = metadata.get("logprob", None)
                ret.token_used = tokens
                ret.trace = trace
                ret.test_fixtures.turns = [response]
                outputs.append(self._form_instance(len(outputs), ret))
        
        return outputs
