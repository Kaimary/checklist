import os
import random
import logging
from munch import Munch
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

# from checklist.spinners import Spinner
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.base_tester import SchemaPruningMixin, BaseTester, ValidationError
from checklist.models import CHESS, DAILSQL, RESDSQL, CODES15b, CODES7b, CSCSQL32b, CSCSQL7b, GenericLLM, OMNISQL32b
from checklist.db_utils.execution import execute_sql, validate_sql_query


class CrossModelTester(SchemaPruningMixin, BaseTester):
    def __init__(self):
        super().__init__("Majority Voting Tester", "cross_model", "differential")
        
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
