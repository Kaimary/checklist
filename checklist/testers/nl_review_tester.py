import os
import logging
import threading
from munch import Munch
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

# from checklist.spinners import Spinner
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.base_tester import SchemaPruningMixin, BaseTester, ValidationError
from checklist.db_utils.execution import validate_sql_query


class NLReviewTester(SchemaPruningMixin, BaseTester):
    def __init__(self):
        super().__init__("Step-through Natural Language Review Tester", "nl_review", "explore")

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
        return passed, ret.test_fixtures, ret.results, ret.avg_logprob, ret.trace
        
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
            while len(paraphrases) < self.num and retry < self.max_retry:
                response, metadata = self.backbone(
                    self.prompt, 
                    self.parser, 
                    request_kwargs={
                        "HINT": self.hint, 
                        "QUESTION": self.nl, 
                        "SQL": self.sql,
                        "NUM": self.num - 1})
                
                self.calls += 1
                self.token_used += metadata.get("token_used", 0)
                
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

                    self.calls += 1
                    self.token_used += metadata.get("token_used", 0)

                    ret.avg_logprob = metadata.get("avg_logprob", None)
                    ret.test_fixtures.turns = response
                    trace += (
                        f"{response['chain_of_thought_reasoning']} -> "
                        f"{response['judgment']}\n"
                    )
                    ret.trace = trace
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