import os
from checklist.llm import LLM
from checklist.test_suite import TestSuite
from checklist.parsers import get_parser
from checklist.prompts import get_prompt
from checklist.database_manager import DatabaseManager
from checklist.database_utils.db_catalog.csv_utils import load_tables_description

class AbstractJudge:
    def __init__(self):
        pass

class LLMJudge(AbstractJudge):
    def __init__(self, model_name: str, enable_few_shot, enable_cot):
        super().__init__()
        self.model_name = model_name
        self.model = LLM(model_name=model_name)
        self.enable_few_shot = enable_few_shot
        self.enable_cot = enable_cot

    def set(self, nl, hint, pred, db_id, db_root_path, schema_file_path, pred_match_gold=None):
        self.nl = nl
        self.hint = hint
        self.pred = pred
        self.db_id = db_id
        self.db_root_path = db_root_path
        self.db_path = os.path.join(db_root_path, db_id, f"{db_id}.sqlite")
        self.schema_file_path = schema_file_path
        self.pred_match_gold = pred_match_gold

        schema = DatabaseManager(db_id=self.db_id, db_root_path=db_root_path).get_db_schema() # type: ignore
        # schema_with_examples = load_schema_with_examples(_get_unique_values(self.db_path))
        schema_with_descriptions = load_tables_description(self.db_path, use_value_description = True)
        self.schema_string = DatabaseManager().get_database_schema_string(
            tentative_schema=schema,
            schema_with_examples=None, # schema_with_examples,
            schema_with_descriptions=schema_with_descriptions,
            include_value_description=True
        )

    def run(self):
        if self.enable_cot:
            parser = get_parser(parser_name="llm_cot_nl2sql_judgment")
            prompt = get_prompt(
                template_name="llm_cot_nl2sql_judgment", 
                schema_string=self.schema_string,
                examples_string="placeholder" if self.enable_few_shot else None
            )
        else:
            parser = get_parser(parser_name="llm_nl2sql_judgment")
            prompt = get_prompt(
                template_name="llm_nl2sql_judgment", 
                schema_string=self.schema_string,
                examples_string="placeholder" if self.enable_few_shot else None
            )
        response = self.model(prompt, parser, request_kwargs={
            "HINT": self.hint, 
            "QUESTION": self.nl,
            "SQL": self.pred
            }
        )
        return response
    
class GuardianJudge(AbstractJudge):
    def __init__(self, *tests):
        super().__init__()
        self.suite = TestSuite()
        # Iterate over the provided test classes and add them to the suite
        for test in tests:
            test_instance = test()  # Create an instance of the test class
            self.suite.add(test_instance, name=f"{test.__name__} Test", capability=f"{test.__name__.lower()}", description=f"{test.__name__} test for SQL correctness")

    def set(self, nl, hint, pred, db_id, db_root_path, schema_file_path, red_schema, pred_match_gold=None):
        self.suite.set(
            nl=nl,
            hint=hint,
            pred=pred,
            db_id=db_id,
            db_root_path=db_root_path,
            schema_file_path=schema_file_path,
            red_schema=red_schema,
            pred_match_gold=pred_match_gold
        )

    def run(self):
        return self.suite.run1()
    
    def summary(self):
        return self.suite.summary1()