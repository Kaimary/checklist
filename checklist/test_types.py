from munch import Munch
from checklist.llm import LLM
from checklist.test_classes import CrossModelTestClass, NLReviewTestClass, OracleResultTestClass, NLRelaxTestClass, \
    NLStrengthenTestClass, SelfConsistencyTestClass, QueryReviewTestClass, SemanticCheckTestClass
from .abstract_test import AbstractTest
from .expect import Expect

class MFT(AbstractTest):
    def __init__(self, data, expect=None, labels=None, meta=None, agg_fn='all',
                 templates=None, name=None, capability=None, description=None):
        """Minimum Functionality Test

        Parameters
        ----------
        data : list
            List or list(lists) of whatever the model takes as input. Strings, tuples, etc.
        expect : function
            Expectation function, takes an AbstractTest (self) as parameter
            see expect.py for details
        labels : single value (int, str, etc) or list
            If list, must be the same length as data
        meta : list
            metadata for examples, must be the same length as data
        agg_fn : function, or string in ['all', 'all_except_first']
            Aggregation function for expect function, if each element in data is a list.
            Takes as input a numpy array, outputs a boolean.
        templates : list(tuple)
            Parameters used to generate the data. Use ret.templates from editor.template
        name : str
            test name
        capability : str
            test capability
        description : str
            test description
        """
        if labels is None and expect is None:
            raise(Exception('Must specify either \'expect\' or \'labels\''))
        if labels is not None and expect is None:
            expect = Expect.eq()
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=False, name=name,
                         capability=capability, description=description)

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
            SemanticCheckTestClass(
                nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, schema_file_path=self.schema_file_path)
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
        self.pred_match_gold = kwargs.get("pred_match_gold", None)
        self.test_classes = [
            OracleResultTestClass(
                nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, num=3)
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
        self.pred_match_gold = kwargs.get("pred_match_gold", None)
        self.test_classes = [
            NLRelaxTestClass(
                nl=self.nl, 
                hint=self.hint, 
                sql=self.pred, 
                db_id=self.db_id, 
                db_root_path=self.db_root_path,
                num=3
            ),
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
                model_list=(["resdsql", "dailsql"] \
                            if "spider" in self.db_root_path else ["cscsql7b", "cscsql32b", "chess", "omnisql32b", "llm:gpt-4o-mini-0708", "llm:gpt-4o-1120"])
            ),
            # SelfConsistencyTestClass(
            #     nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, 
            #     num=3, model=LLM(model_name="gpt-4o-mini-0708"))
        ]

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
        self.pred_match_gold = kwargs.get("pred_match_gold", None)
        self.test_classes = [
            QueryReviewTestClass(nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, num=3),
            NLReviewTestClass(nl=self.nl, hint=self.hint, sql=self.pred, db_id=self.db_id, db_root_path=self.db_root_path, num=3)
        ]