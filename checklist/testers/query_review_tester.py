import os
import copy
import random
import logging
import threading
from munch import Munch
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

# from checklist.spinners import Spinner
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.red.parser.red_parser import Query
from checklist.base_tester import SchemaPruningMixin, BaseTester, ValidationError
from checklist.db_utils.execution import validate_sql_query

class QueryReviewTester(SchemaPruningMixin, BaseTester):
    def __init__(self):
        super().__init__("Step-through Query Review Tester", "query_review", "explore")

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
        return passed, ret.test_fixtures, ret.results, ret.avg_logprob, ret.trace
    
    def _validate_test_fixture(self, response):
        def __output_format_check(response):
            if not isinstance(response, dict):
                raise ValidationError(
                    f"Output format(type) check failed. "
                    f"response type: {type(response)}, "
                    f"Expected type: dict"
                )
            if "judgment" not in response.keys():
                raise ValidationError(
                    f"Output format(key) check failed. "
                    f"Keys found in response: {','.join(response.keys())}, "
                    f"Expected keys: `judgment`"
                )
            return True
        
        # output format check
        __output_format_check(response)

        return


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
            sql = " ".join(query.clauses[name].sql_str for name in Query._check_order if name in active)
            sub_sqls = [sql]

            for clause in order:
                active.add(clause)
                sql = " ".join(
                    query.clauses[name].sql_str
                    for name in Query._check_order
                    if name in active
                )
                sub_sqls.append(sql.strip())
            return sub_sqls
        def _format_sub_sqls_with_results(sub_sqls):
            final_sql_output, output = "", ""
            for idx, sub_sql in enumerate(sub_sqls, 1):
                exec = validate_sql_query(self.db_path, sub_sql, max_returned_rows=10)
                preview = exec.get("RESULT")
                note = f"(showing first 5 rows)" if isinstance(preview, list) and len(preview) > 5 else ""
                err = "[Error]" if isinstance(preview, str) else ""
                if idx == len(sub_sqls):
                    final_sql_output = f"Execution{note}: {err}{preview[:5]}"
                else: 
                    output += f"Sub-SQL{idx}: {sub_sql}\nExecution{note}: {err}{preview[:5]}\n"
            return final_sql_output, output
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
            res, subsql_context = _prepare_subsql_context()
            response, metadata = self.backbone(
                self.prompt,
                self.parser,
                request_kwargs={
                    "HINT": self.hint,
                    "QUESTION": self.nl,
                    "SQL": self.sql,
                    "RESULT": res,
                    "SUBSQLS": subsql_context
                }
            )

            self.calls += 1
            self.token_used += metadata.get("token_used", 0)

            ret.avg_logprob = metadata.get("avg_logprob", None)
            ret.test_fixtures.turns = response
            trace += f"{response.get('chain_of_thought_reasoning', '')}\n"
            trace += f"{response.get('judgment', '')}"
            ret.trace = trace
            return response, ret

        retry = 0
        state_lock = threading.Lock()
        # spinner = Spinner(f"Generating test cases of `{self.name}` ...")
        
        def submit_task(executor, futures):
            with state_lock:
                outstanding = len(self.test_cases) + len(futures)
                if outstanding >= self.num or retry >= self.max_retry:
                    return False
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
                        response, ret = fut.result()
                    except Exception as exc:
                        logging.warning(f"Query review test case generation failed: {exc}")
                        with state_lock:
                            retry += 1
                            # if verbose:
                            #     spinner.set_message(f"Test fixture generation failed (attempt {retry}/{self.max_retry})...")
                            stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry
                        continue
                    
                    try:
                        with state_lock:
                            self._validate_test_fixture(response)
                            self.test_cases.append(self._form_instance(len(self.test_cases), ret))
                            # spinner.set_message(f"Generated {len(outputs)} test cases ...")
                            stop_generation = len(self.test_cases) >= self.num or retry >= self.max_retry
                            if stop_generation: break
                    except ValidationError as e:
                        with state_lock:
                            retry += 1
                            # if verbose:
                            #     spinner.set_message(f"Test fixture validation failed (attempt {retry}/{self.max_retry})...")
                        logging.warning(f"Test fixture validation failed: {e}")
                    except Exception as err:
                        with state_lock:
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