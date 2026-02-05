import os
import re
import time
import json
import copy
import random
import shutil
import logging
import threading
import numpy as np
from munch import Munch
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

# from checklist.spinner import Spinner
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.red.parser.report import BugLevel
from checklist.red.parser.red_parser import Query
from checklist.database_manager import DatabaseManager
from checklist.database_utils.schema_generator import DatabaseSchemaGenerator
from checklist.base_test_class import SchemaPruningMixin, TestClass, ValidationError
from checklist.models import CHESS, DAILSQL, RESDSQL, CODES15b, CODES7b, CSCSQL32b, CSCSQL7b, GenericLLM, OMNISQL32b
from checklist.database_utils.sql_parser import is_sql_do_math
from checklist.database_utils.execution import execute_sql, validate_sql_query
from checklist.database_utils.db_opt import create_sqlite_database, duplicate_sqlite_database, insert_rows_into_table, sqlite_type_map

class SemanticCheckTestClass(TestClass):
    def __init__(self):
        super().__init__("Semantic Check Test Class", "semantic_check", "semantic", key="sql")

    def set(self, red_schema, **kwargs):
        super().set(**kwargs)
        self.schema = red_schema

    def _compare_query_results(self, pred):
        if pred: return False
        return True
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.warnings = [bug for bug in ret.test_fixtures.bugs if type(bug) != str and bug.level == BugLevel.WARNING]
        ret.results.pred = [bug for bug in ret.test_fixtures.bugs if type(bug) == str or bug.level == BugLevel.ERROR]
        ret.results.standard = "pred is empty"
        passed = self._compare_query_results(ret.results.pred)
        return passed, ret.test_fixtures, ret.results, None, 0, ""
    
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
        
        bugs = []
        # spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        # with spinner:
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
        self.test_cases.append(self._form_instance(len(self.test_cases), ret))
        del parsed_query

        return

class OracleResultTestClass(SchemaPruningMixin, TestClass):
    def __init__(self):
        super().__init__("Oracle Result Test Class", "oracle_result", "oracle", key="nl")

    def set(self, pruning_threshold=20, **kwargs):
        super().set(**kwargs)
        self.num=3
        self.criteria=0.6
        self.max_retry = self.num
        self.parallel_workers = self.num
        self.parser = get_parser(parser_name="simulate_db_generation")
        self.parser2 = get_parser(parser_name="oracle_data_generation")
        self.schema, self.schema_pruned = self._get_db_schema(pruning_threshold)
        self._schedule_pruned_db_materialization(self.schema_string)
        # self.test_cases = self._generator()
        # logging.info(f"Generate tests took {time.time() - start:.2f} seconds.")   

    def _compare_query_results(self, preds, oracles, do_math=False):
        def __normalize_scalar(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return round(float(value), 2)
            if isinstance(value, str):
                stripped = value.strip()
                try:
                    return round(float(stripped), 2)
                except ValueError:
                    return value
            return value
        def __freeze(obj):
            """Recursively convert unhashable objects into hashable equivalents."""
            if isinstance(obj, dict):
                return tuple(sorted((k, __freeze(v)) for k, v in obj.items()))
            elif isinstance(obj, tuple) or isinstance(obj, list):
                items = [__freeze(x) for x in obj]
                try:
                    return tuple(sorted(items))
                except TypeError:
                    return tuple(sorted(items, key=lambda v: repr(v)))
            elif isinstance(obj, set):
                items = [__freeze(x) for x in obj]
                try:
                    return tuple(sorted(items))
                except TypeError:
                    return tuple(sorted(items, key=lambda v: repr(v)))
            elif isinstance(obj, np.ndarray):
                return __freeze(tuple(obj.tolist()))
            else:
                return __normalize_scalar(obj)
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
        # if the simulated database can't execute the sql (both empty-results), most probably the simulated database made something wrong...
        # in this case, make it pass to avoid high false negative rate
        if not preds or not oracles: return True
        if do_math and preds[0] == (None,): return True
        if len(set(preds)) != len([tuple(x) for x in oracles]): return False

        preds_frozen = [__freeze(p) for p in preds]
        oracle_frozen = [__freeze(o) for o in oracles]

        for p in preds_frozen:
            if not any(__is_subset(p, o) or __is_subset(o, p) for o in oracle_frozen):
                return False
        return True
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        # Test the original SQL over a faked database with expected execution results
        res = validate_sql_query(ret.test_fixtures.db, self.sql, max_returned_rows="all")
        logging.info(f"Validating SQL: {self.sql}")
        ret.results.pred = res['RESULT'] if res['STATUS'] == 'OK' else None
        ret.results.target = ret.test_fixtures.oracle["rows"] if "rows" in ret.test_fixtures.oracle.keys() else []
        logging.info(f"Predicted Result: {ret.results.pred}, Target Result: {ret.results.target}")
        ret.results.standard = "pred == target"
        passed = self._compare_query_results(ret.results.pred, ret.results.target, do_math=is_sql_do_math(self.sql))
        return passed, ret.test_fixtures, ret.results, ret.logprob, ret.token_used, ret.trace

    def _validate_test_fixture(self, response):
        def __output_format_check(response):
            if not isinstance(response, dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"response type: {type(response)}, "
                    f"Expected type: dict"
                )
            if 'data' not in response.keys():
                raise ValidationError(
                    f"Output format(key) check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `data`"
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
        def __normalize_sqlite_type(tp: str) -> str:
            """Normalize SQLite type (case-insensitive, strip length, etc.)."""
            tp = tp.upper().strip()
            # Remove size qualifiers, e.g., VARCHAR(20) -> VARCHAR
            tp = re.sub(r'\s*\(.*\)', '', tp)
            return tp          
        def __schema_data_alignment_check(response):
            tables = DatabaseManager().get_db_all_tables() if not self.schema_pruned else [k for k in self.schema.keys()]
            col_types= DatabaseManager().get_all_column_types() if not self.schema_pruned \
                else __extract_column_types_from_schema_string(self.schema_string)
            
            # table name validity check
            tables_in_data = response["data"].keys()
            for td in tables_in_data:
                if td not in tables:
                    raise ValidationError(
                        f"Table name checking failed. "
                        f"Non-existed table name found in generated data: {td} "
                        f"Existing table names: {','.join(tables)}"
                    )
            # column count and data types consistent check
            for t, rows in response["data"].items():
                if not rows: continue
                if len(col_types[t]) != len(rows[0]):
                    raise ValidationError(
                        f"Schema-data column count mismatch. "
                        f"Column count in data row: {len(rows[0])}(e.g., {rows[0]}), "
                        f"Expected column count of table {t}: {len(col_types[t])}({','.join(self.schema[t])})"
                    )
                for v, tp in zip(rows[0], col_types[t]):
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
        # output format check
        __output_format_check(response)
        # schema-data alignment check
        __schema_data_alignment_check(response)

    def _validate_test_fixture2(self, response, history):
        def __output_format_check(response):
            if not isinstance(response, dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"response type: {type(response)}, "
                    f"Expected type: dict"
                )
            if 'result' not in response.keys():
                raise ValidationError(
                    f"Output format(key) check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `result`"
                )
            if not isinstance(response["result"], dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"`result` type: {type(response['result'])}, "
                    f"Expected type: dict"
                )
            # quick fix format issues frequently observed
            if "columns" in response["result"].keys() and "rows" in response.keys(): response = {"result": response}
            if any(k not in response["result"].keys() for k in ["columns", "rows"]): 
                raise ValidationError(
                    f"Output format(key in key) check failed. "
                    f"Keys found in `result`: {','.join(response['result'].keys())}, "
                    f"Expected keys: `columns` and `rows`"
                )
            if isinstance(response["result"]["rows"], list) and not isinstance(response["result"]["rows"][0], list):
                response["result"]["rows"] = [response["result"]["rows"]]
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
                if not __dicts_equal___(response["data"], h["data"]): continue
                # Check whether result is the same or not if test cases are same. If it is the case, drop it
                # if __dicts_equal___(response["result"], h["oracle"]): 
                raise ValidationError("Duplicate(`data`+`result`) test case.")
                # # Otherwise, double check which `result` is the correct one
                # retry = 0
                # prompt = get_prompt(template_name="oracle_result_checking", schema_string=self.schema_string)
                # parser = get_parser(parser_name="oracle_result_checking")
                # while True and retry < self.max_retry:
                #     response2, _ = self.backbone(prompt, parser, request_kwargs={
                #         "HINT": self.hint,
                #         "QUESTION": self.nl,
                #         "INSTANCES": json.dumps(h['data'], indent=4),
                #         "RESULT1": json.dumps(h['oracle'], indent=4),
                #         "RESULT2": json.dumps(response["result"], indent=4)
                #     })
                #     if isinstance(response2, dict):
                #         if "result" in response2.keys(): break
                #         if "columns" in response2.keys() and "rows" in response2.keys():
                #             response2 = {"result": response2}
                #             break
                # # Modify the `result` according to the output (TODO further check its correctness?)
                # h['oracle'] = response2["result"]
            return True
        
        # output format check
        __output_format_check(response)
        # response duplication check
        __response_history_compatible_check(response, history)

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
        #     database=ret.test_fixtures.db, sql=self.sql, expect=ret.test_fixtures.oracle)
        
        return ret

    def _generator(self, verbose=True):
        def __history_to_string(history):
            return "\n".join(
                f"--- Example {i+1} ---\n"
                f"data: {json.dumps(h['data'], indent=4)}\n\n"
                for i, h in enumerate(history)
            )
        def __values_to_string(vals):
            return "\n".join(
                f"Column `{t}.{c}`: {', '.join(v)};"
                for t, c2vals in vals.items()
                for c, v in c2vals.items()
            )

        history = []
        retry = 0
        # spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        cond_literals = DatabaseManager(db_id=self.db_id, db_root_path=self.db_root_path).get_sql_condition_literals(self.sql)
        state_lock = threading.Lock()

        def _generate_candidate(history_string):
            ret = Munch()
            ret.test_fixtures = Munch()
            tokens, logprob = 0, 0
            trace = "->>Parallel Test Case Tracelog<<-\n"
            prompt = get_prompt(
                template_name="simulate_db_generation",
                schema_string=self.schema_string,
                columns_values_string=__values_to_string(cond_literals) if cond_literals else None,
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
            trace += f"[simulated DB]: {response.get('data', '')}"
            # logging.info(f"simulate_db_generation took {time.time() - start:.2f} seconds.")

            prompt2 = get_prompt(template_name="oracle_data_generation", schema_string=self.schema_string)
            response2, metadata2 = self.backbone(
                prompt2,
                self.parser2,
                request_kwargs={
                    "QUESTION": self.nl,
                    "HINT": self.hint,
                    "DATABASE_INSTANCES": json.dumps(response.get("data", {}), indent=4)
                }
            )
            metadata2 = metadata2 or {}
            tokens += metadata2.get("token_used", 0)
            logprob += metadata2.get("logprob", None)
            ret.logprob = logprob*0.5
            ret.token_used = tokens
            trace += f"[oracle data]: {response2.get('result', '')}"
            ret.trace = trace
            ret.test_fixtures.data = response.get("data", {})
            ret.test_fixtures.oracle = response2.get("result", {})
            
            return response, response2, ret

        def submit_task(executor, futures):
            with state_lock:
                if len(self.test_cases) >= self.num or retry >= self.max_retry:
                    return False
                history_string = __history_to_string(history) if history else None
            future = executor.submit(_generate_candidate, history_string)
            futures.add(future)
            return True

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            futures = set()
            for _ in range(self.parallel_workers):
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
                            # if verbose:
                            #     spinner.set_message(f"Test fixture generation failed (attempt {retry}/{self.max_retry})...")
                            stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry
                        continue

                    appended_to_history = False
                    try:
                        with state_lock:
                            self._validate_test_fixture(response)
                            response2["data"] = response.get("data") # append data instances into response2 for history duplicate validation 
                            self._validate_test_fixture2(response2, history)
                            history.append(ret.test_fixtures)
                            appended_to_history = True
                            self.test_cases.append(self._form_instance(len(self.test_cases), ret))
                            # spinner.set_message(f"Generated {len(outputs)} test cases ...")
                            stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry
                            if stop_generation:
                                break
                    except ValidationError as e:
                        with state_lock:
                            if appended_to_history and history:
                                history.pop()
                            retry += 1
                            # if verbose:
                            #     spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                            stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry
                        logging.warning(f"Test fixture validation failed: {e}")
                    except Exception as err:
                        with state_lock:
                            if appended_to_history and history:
                                history.pop()
                            retry += 1
                            # if verbose:
                            #     spinner.set_message(f"Test fixture materialization failed (attempt {retry}/{self.max_retry})...")
                            stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry
                        logging.exception("Failed to materialize oracle test instance", exc_info=err)

                    if stop_generation:
                        break

                    submit_task(executor, futures)

            for fut in futures:
                fut.cancel()

        return

class NoiseRowTestClass(SchemaPruningMixin, TestClass):
    def __init__(self):
        super().__init__("Noise Row Injection Test Class", "metamorphic_noise", "metamorphic")

    def set(self, pruning_threshold=20, **kwargs):
        super().set(**kwargs)
        self.criteria=0.6
        self.num=3
        self.max_retry=self.num
        self.parallel_workers = self.num
        self.parser = get_parser(parser_name="noise_data_injection")
        self.schema, self.schema_pruned = self._get_db_schema(pruning_threshold)
        self._schedule_pruned_db_materialization(self.schema_string, copy_existing_rows=True)
        # self.test_cases = self._generator()

    def _compare_query_results(self, preds, oracles):
        if not preds or not oracles: return False
        return len(preds) == len(oracles)
    
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
        history = []
        # spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        state_lock = threading.Lock()

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
                if len(self.test_cases) >= self.num or retry >= self.max_retry: return False
                history_string = __history_to_string(history) if history else None
            future = executor.submit(_generate_candidate, history_string)
            futures.add(future)
            return True

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            futures = set()
            for _ in range(self.parallel_workers):
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
                            # if verbose:
                            #     spinner.set_message(f"Test fixture generation failed (attempt {retry}/{self.max_retry})...")
                        continue

                    appended_to_history = False
                    try:
                        with state_lock:
                            self._validate_test_fixture(response, history)
                            history.append(ret.test_fixtures)
                            appended_to_history = True
                            self.test_cases.append(self._form_instance(len(self.test_cases), ret))
                            # spinner.set_message(f"Generated {len(outputs)} test cases ...")
                            stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry
                            if stop_generation: break
                    except ValidationError as e:
                        with state_lock:
                            if appended_to_history and history:
                                history.pop()
                            retry += 1
                            # if verbose:
                            #     spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                        logging.warning(f"Test fixture validation failed: {e}")
                    except Exception as err:
                        with state_lock:
                            if appended_to_history and history:
                                history.pop()
                            retry += 1
                            # if verbose:
                            #     spinner.set_message(f"Test fixture materialization failed (attempt {retry}/{self.max_retry})...")
                        logging.exception("Failed to materialize test instance", exc_info=err)

                    with state_lock:
                        stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry

                    if stop_generation:
                        break

                    submit_task(executor, futures)

            for fut in futures:
                fut.cancel()

        return

class CrossModelTestClass(SchemaPruningMixin, TestClass):
    def __init__(self):
        super().__init__("Majority Voting Test Class", "majority_vote", "differential")
        
    def set(self, pruning_threshold=20, **kwargs):
        super().set(**kwargs)
        self.num=3
        self.active_model_num = 3
        model_list=(["resdsql", "codes15b", "dailsql", "llm:gpt-5.1"] if "spider" in self.db_root_path else \
                     ["chess", "cscsql32b", "omnisql32b", "llm:gpt-5.1"])
        self.model_pool = self._create_nl2sql_model_pool(model_list)
        self.schema, self.schema_pruned = self._get_db_schema(pruning_threshold)
        # self.test_cases = self._generator()

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
        return passed, ret.test_fixtures, ret.results, None, 0, ""
    
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

        prompt = get_prompt(template_name="nl2sql_translation", schema_string=self.schema_string)
        parser = get_parser(parser_name="nl2sql_translation")
        invalids = set()
        retry = 0
        # spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        while len(self.test_cases) < self.num and retry < self.max_retry:
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
                        # if verbose: spinner.set_message(f"Candidate SQL validation failed: {e} ...")
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
                # if verbose: spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                continue
            ret.test_fixtures.candidates = candidates
            self.test_cases.append(self._form_instance(len(self.test_cases), ret))
            # spinner.set_message(f"Generated {len(outputs)} test cases ...")
        return

class QueryReviewTestClass(SchemaPruningMixin, TestClass):
    def __init__(self):
        super().__init__("Step-through Query Review Test Class", "query_review", "explore")

    def set(self, red_schema, pruning_threshold=20, **kwargs):
        super().set(**kwargs)
        self.criteria=0.6
        self.num=3
        self.max_retry=self.num
        self.parallel_workers = self.num
        self.red_schema = red_schema
        self.schema, self.schema_pruned = self._get_db_schema(pruning_threshold)
        self.parser = get_parser(parser_name="query_rubber_duck_debugging")
        self.prompt = get_prompt(template_name="query_rubber_duck_debugging", schema_string=self.schema_string)
        # self.test_cases = self._generator()
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = ret.test_fixtures.turns['judgment']
        ret.results.target = True
        ret.results.standard = "pred == target"
        passed = ret.results.pred
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
    
    def _generator(self, verbose=True):
        def _build_sub_sqls(query: Query):
            order = [c for c in Query._check_order if c in query.clauses and c not in ["SELECT", "FROM"]]
            random.shuffle(order)
            # always make `SELECT ... FROM ...` subsql as the first one to ensure each subsqls executable
            active = {"SELECT", "FROM"}
            sql = " ".join(query.clauses[name].sql_str for name in active)
            sub_sqls = [sql]

            for clause in order:
                active.add(clause)
                sql = " ".join(
                    query.clauses[name].sql_str
                    for name in Query._check_order
                    if name in active
                )
                sub_sqls.append(sql.strip())
            # for i, s in enumerate(sub_sqls, 1):
            #     print(f"sub-sqls{i}: {s}")
            return sub_sqls
        def _format_sub_sqls_with_results(sub_sqls):
            output = ""
            for idx, sub_sql in enumerate(sub_sqls, 1):
                exec = validate_sql_query(self.db_path, sub_sql, max_returned_rows=5)
                preview = exec.get("RESULT")
                err = "[Error]" if isinstance(preview, str) else ""
                output += f"Sub-SQL{idx}: {sub_sql}\nExecution: {err}{preview}\n"
            return output
        def _prepare_subsql_context():
            try:
                parsed_query = Query(self.sql, copy.deepcopy(self.red_schema))
                subsqls = _build_sub_sqls(parsed_query)
                return _format_sub_sqls_with_results(subsqls)
            except Exception as e:
                logging.warning(f"Failed to derive intermediate SQL steps: {e}")
                return f"Failed to derive intermediate SQL steps due to: {e}"
        def _generate_case():
            ret = Munch()
            ret.test_fixtures = Munch()
            trace = "->>Parallel Test Case Tracelog<<-\n"
            subsql_context = _prepare_subsql_context()
            response, metadata = self.backbone(
                self.prompt,
                self.parser,
                request_kwargs={
                    "HINT": self.hint,
                    "QUESTION": self.nl,
                    "SQL": self.sql,
                    "SUBSQLS": subsql_context
                }
            )
            metadata = metadata or {}
            trace += f"{response.get('chain_of_thought_reasoning', '')}\n"
            trace += f"{response.get('judgment', '')}"
            ret.token_used = metadata.get("token_used", 0)
            ret.logprob = metadata.get("logprob", None)
            ret.test_fixtures.turns = response
            ret.trace = trace
            return ret

        retry = 0
        # spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        state_lock = threading.Lock()

        def submit_task(executor, futures):
            with state_lock:
                if len(self.test_cases) >= self.num or retry >= self.max_retry: return False
            future = executor.submit(_generate_case)
            futures.add(future)
            return True

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            futures = set()
            for _ in range(self.parallel_workers):
                if not submit_task(executor, futures):
                    break

            stop_generation = False
            while futures and not stop_generation:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done:
                    futures.remove(fut)
                    try:
                        ret = fut.result()
                    except Exception as exc:
                        logging.warning(f"Query review test case generation failed: {exc}")
                        retry += 1
                        # if verbose:
                        #     spinner.set_message(f"Generation failed (attempt {retry}/{self.max_retry})...")
                    else:
                        with state_lock:
                            self.test_cases.append(self._form_instance(len(self.test_cases), ret))
                            # if verbose:
                            #     spinner.set_message(f"Generated {len(outputs)} test cases ...")

                    stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry
                    if stop_generation:
                        break

                    submit_task(executor, futures)

            for fut in futures:
                fut.cancel()

        return

class NLReviewTestClass(SchemaPruningMixin, TestClass):
    def __init__(self):
        super().__init__("Step-through Natural Language Review Test Class", "nl_review", "explore")

    def set(self, pruning_threshold=20, **kwargs):
        super().set(**kwargs)
        self.num = 3
        self.criteria = 0.6
        self.max_retry = self.num
        self.parallel_workers = self.num
        self.schema, self.schema_pruned = self._get_db_schema(pruning_threshold)
        self.prompt = get_prompt(template_name="nl_paraphrase_generation", schema_string=self.schema_string)
        self.prompt2 = get_prompt(template_name="nl_rubber_duck_debugging", schema_string=self.schema_string)
        self.parser = get_parser(parser_name="nl_paraphrase_generation")
        self.parser2 = get_parser(parser_name="nl_rubber_duck_debugging")
        # self.test_cases = self._generator()
    
    def _test_fn(self, ret: Munch):
        ret.results = Munch()
        ret.results.pred = ret.test_fixtures.turns['judgment']
        ret.results.target = True
        ret.results.standard = "pred == target"
        passed = ret.results.pred
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
    
    def _validate_test_fixture(self, response, key="paraphrases"):
        def __output_format_check(response, key):
            if not isinstance(response, dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"response type: {type(response)}, "
                    f"Expected type: dict"
                )
            if key not in response.keys():
                raise ValidationError(
                    f"Output format(key) check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `{key}`"
                )
            return True

        __output_format_check(response, key)
        if not isinstance(response[key], list) or len(response[key]) < 2:
            raise ValidationError(
                f"Paraphrase count check failed. "
                f"Expected at least 2 paraphrases, got {len(response[key]) if isinstance(response[key], list) else 'invalid'}."
            )
       
    def _generator(self, verbose=True):
        def _prepare_paraphrases():
            retry = 0
            paraphrases = [self.nl]
            while len(paraphrases) < 3 and retry < self.max_retry:
                response, _ = self.backbone(self.prompt, self.parser, request_kwargs={"HINT": self.hint, "QUESTION": self.nl, "SQL": self.sql})
                try:
                    self._validate_test_fixture(response)
                except ValidationError as e:
                    attempts += 1
                    logging.warning(f"Paraphrase validation failed (attempt {attempts}/{self.max_retry}): {e}")
                    continue
                paraphrases.extend(response["paraphrases"])
            return paraphrases
        def _generate_candidate(paraphrase):
            attempts = 0
            last_exc = None
            while attempts < self.max_retry:
                attempts += 1
                try:
                    ret = Munch()
                    ret.test_fixtures = Munch()
                    trace = f"->>Parallel Test Case Tracelog<<-\n"
                    response, metadata = self.backbone(
                        self.prompt2,
                        self.parser2,
                        request_kwargs={
                            "HINT": self.hint, 
                            "QUESTION": paraphrase, 
                            "SQL": self.sql,
                            "RESULT": '\n'.join(f'{tup}' for tup in preview) \
                                if isinstance(preview, list) else preview},
                    )
                    trace += (
                        f"{response['chain_of_thought_reasoning']} -> "
                        f"{response['judgment']}\n"
                    )
                    ret.logprob = metadata.get("logprob", None)
                    ret.token_used = metadata.get("token_used", 0)
                    ret.trace = trace
                    ret.test_fixtures.turns = response
                    return ret
                except Exception as exc:
                    last_exc = exc
                    logging.warning(
                        f"NL review test case generation failed (attempt {attempts}/{self.max_retry}): {exc}"
                    )
            if last_exc:
                raise last_exc
            raise RuntimeError("NL review paraphrase generation failed without exception.")

        paraphrases = _prepare_paraphrases()
        exec = validate_sql_query(self.db_path, self.sql, max_returned_rows=5)
        preview = exec.get("RESULT")
        # spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        state_lock = threading.Lock()
        paraphrase_queue = deque(paraphrases)
        futures = {}

        def submit_task(executor):
            with state_lock:
                if not paraphrase_queue:
                    return False
                paraphrase = paraphrase_queue.popleft()
            future = executor.submit(_generate_candidate, paraphrase)
            futures[future] = paraphrase
            return True

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            for _ in range(min(self.parallel_workers, len(paraphrases))):
                if not submit_task(executor):
                    break

            while futures and len(self.test_cases) < self.num:
                done, _ = wait(set(futures.keys()), return_when=FIRST_COMPLETED)
                for fut in done:
                    paraphrase = futures.pop(fut)
                    try:
                        case_ret = fut.result()
                    except ValidationError as e:
                        logging.warning(f"NL review test fixture validation failed: {e}")
                        with state_lock:
                            paraphrase_queue.append(paraphrase)
                    except Exception as exc:
                        logging.warning(f"NL review test case generation failed: {exc}")
                        with state_lock:
                            paraphrase_queue.append(paraphrase)
                    else:
                        with state_lock:
                            self.test_cases.append(self._form_instance(len(self.test_cases), case_ret))
                            # if verbose:
                            #     spinner.set_message(f"Generated {len(outputs)} test cases ...")

                    if len(self.test_cases) >= self.num:
                        break

                    submit_task(executor)

            for fut in futures:
                fut.cancel()

        return