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
from checklist.base_tester import SchemaPruningMixin, BaseTester
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