from checklist.test_classes import CrossModelTestClass, MinimumSyntaxTestClass, NLReviewTestClass, OracleResultTestClass, NLRelaxTestClass, \
    NLStrengthenTestClass, SelfConsistencyTestClass, QueryReviewTestClass, SemanticCheckTestClass
from .abstract_test import AbstractTest
from .expect import Expect

class SEM(AbstractTest):
    """Semantic Check Testing (RED)

        Parameters
        ----------
        data : list
            List or list(lists) of whatever the model takes as input. Strings, tuples, etc.
        expect : function
            Expectation function, takes an AbstractTest (self) as parameter
            see expect.py for details.
    """
    def __init__(self, use_cache=True, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        self.key = "sql+schema"
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description)
    
    def set(self, **kwargs):
        self.nl = kwargs.get("nl", None)
        self.hint = kwargs.get("hint", None)
        self.pred = kwargs.get("pred", None)
        self.db_id = kwargs.get("db_id", None)
        self.db_root_path = kwargs.get("db_root_path", None)
        self.schema_file_path = kwargs.get("schema_file_path", None)
        self.pred_match_gold = kwargs.get("pred_match_gold", None)
        self.test_classes = [
            MinimumSyntaxTestClass(nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path),
            SemanticCheckTestClass(
                nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, schema_file_path=self.schema_file_path
            )
        ]

class ORC(AbstractTest):
    """Oracle-based Testing

        Parameters
        ----------
        data : list
            List or list(lists) of whatever the model takes as input. Strings, tuples, etc.
        expect : function
            Expectation function, takes an AbstractTest (self) as parameter
            see expect.py for details.
    """
    def __init__(self, use_cache=True, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        self.key = "nl+schema"
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description)

    def set(self, **kwargs):
        self.nl = kwargs.get("nl", None)
        self.hint = kwargs.get("hint", None)
        self.pred = kwargs.get("pred", None)
        self.db_id = kwargs.get("db_id", None)
        self.db_root_path = kwargs.get("db_root_path", None)
        self.schema_file_path = kwargs.get("schema_file_path", None)
        self.pred_match_gold = kwargs.get("pred_match_gold", None)
        self.test_classes = [
            OracleResultTestClass(
                nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, schema_file_path=self.schema_file_path, criteria=0.6, num=3)
        ]

class MTP(AbstractTest):
    """
    Metamorphic Testing
    
        Parameters
        ----------
        data : list
            List or list(lists) of whatever the model takes as input. Strings, tuples, etc.
        expect : function
            Expectation function, takes an AbstractTest (self) as parameter
            see expect.py for details.
    """
    def __init__(self, use_cache=True, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        self.key = "nl+schema+sql"
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description)
    
    def set(self, **kwargs):
        self.nl = kwargs.get("nl", None)
        self.hint = kwargs.get("hint", None)
        self.pred = kwargs.get("pred", None)
        self.db_id = kwargs.get("db_id", None)
        self.db_root_path = kwargs.get("db_root_path", None)
        self.schema_file_path = kwargs.get("schema_file_path", None)
        self.pred_match_gold = kwargs.get("pred_match_gold", None)
        self.test_classes = [
            # NLRelaxTestClass(
            #     nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, schema_file_path=self.schema_file_path, num=3),
            NLStrengthenTestClass(
                nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, num=3)
        ]
        
class DIF(AbstractTest):
    """
    Differential Testing
    
        Parameters
        ----------
        data : list
            List or list(lists) of whatever the model takes as input. Strings, tuples, etc.
        expect : function
            Expectation function, takes an AbstractTest (self) as parameter
            see expect.py for details.
    """
    def __init__(self, use_cache=True, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        self.key = "nl+schema"
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description)

    def set(self, **kwargs):
        self.nl = kwargs.get("nl", None)
        self.hint = kwargs.get("hint", None)
        self.pred = kwargs.get("pred", None)
        self.db_id = kwargs.get("db_id", None)
        self.db_root_path = kwargs.get("db_root_path", None)
        self.pred_match_gold = kwargs.get("pred_match_gold", None)
        self.test_classes = [
            CrossModelTestClass(
                nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, num=3, 
                model_list=(["resdsql", "codes15b", "dailsql", "llm:deepseek-chat"] \
                            if "spider" in self.db_root_path else ["chess", "cscsql32b", "omnisql32b", "llm:deepseek-chat"])
            )
        ]
        if "spider" in self.db_root_path:
            self.test_classes.append(
                SelfConsistencyTestClass(
                    nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, criteria=0.3, num=3
                )
            )

class EXP(AbstractTest):
    """
    Exploratory Testing
    
        Parameters
        ----------
        data : list
            List or list(lists) of whatever the model takes as input. Strings, tuples, etc.
        expect : function
            Expectation function, takes an AbstractTest (self) as parameter
            see expect.py for details.
    """
    def __init__(self, use_cache=True, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        self.key = "nl+schema+sql"
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description)
        
    def set(self, **kwargs):
        self.nl = kwargs.get("nl", None)
        self.hint = kwargs.get("hint", None)
        self.pred = kwargs.get("pred", None)
        self.db_id = kwargs.get("db_id", None)
        self.db_root_path = kwargs.get("db_root_path", None)
        self.schema_file_path = kwargs.get("schema_file_path", None)
        self.pred_match_gold = kwargs.get("pred_match_gold", None)
        self.test_classes = [
            QueryReviewTestClass(nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, schema_file_path=self.schema_file_path, num=1),
            NLReviewTestClass(nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, num=1)
        ]