from munch import Munch
from checklist.llm import LLM
from checklist.test_cases import CrossModelTestCase, NLReviewTestCase, OracleResultTestCase, NLRelaxTestCase, \
    NLStrengthenTestCase, SelfConsistencyTestCase, QueryReviewTestCase, SemanticCheckTestCase
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
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, schema_file,
                 use_cache=True, pred_match_gold=None, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description, pred_match_gold=pred_match_gold)
        
        self.nl = nl
        self.sql = sql
        self.sql_dialect=sql_dialect
        self.db_id = db_id
        self.db_path = db_path
        self.schema_file = schema_file

        self.key = "sql+schema"
        
        self.test_cases = [
            SemanticCheckTestCase(nl, hint, sql, sql_dialect, db_id, db_path, schema_file)
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
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, 
                 use_cache=True, pred_match_gold=None, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description, pred_match_gold=pred_match_gold)
        
        self.nl = nl
        self.sql = sql
        self.sql_dialect=sql_dialect
        self.db_id = db_id
        self.db_path = db_path
        
        self.key = "nl+schema"
        
        self.test_cases = [
            OracleResultTestCase(nl, hint, sql, sql_dialect, db_id, db_path, num=5)
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
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, 
                 use_cache=True, pred_match_gold=None, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description, pred_match_gold=pred_match_gold)
        
        self.nl = nl
        self.hint = hint
        self.sql = sql
        self.sql_dialect=sql_dialect
        self.db_id = db_id
        self.db_path = db_path
        
        self.key = "nl+schema+sql"
        
        self.test_cases = [
            NLRelaxTestCase(nl, hint, sql, sql_dialect, db_id, db_path, num=3),
            NLStrengthenTestCase(nl, hint, sql, sql_dialect, db_id, db_path, num=3)
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
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path, 
                 use_cache=True, pred_match_gold=None, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description, pred_match_gold=pred_match_gold)
        
        self.nl = nl
        self.hint = hint
        self.sql = sql
        self.sql_dialect=sql_dialect
        self.db_id = db_id
        self.db_path = db_path
        
        self.key = "nl+schema"
        
        self.test_cases = [
            CrossModelTestCase(nl, hint, sql, sql_dialect, db_id, db_path, num=3),
            SelfConsistencyTestCase(nl, hint, sql, sql_dialect, db_id, db_path, num=3, model=LLM(model_name="gpt-4o-mini-0708"))
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
    def __init__(self, nl, hint, sql, sql_dialect, db_id, db_path,
                 use_cache=True, pred_match_gold=None, data=None, expect=None, meta=None, agg_fn='all_except_first',
                 templates=None, name=None, labels=None, capability=None, description=None):

        expect = Expect.eq()
        super().__init__(data, expect, labels=labels, meta=meta, agg_fn=agg_fn,
                         templates=templates, print_first=True, name=name,
                         capability=capability, description=description, pred_match_gold=pred_match_gold)
        
        self.nl = nl
        self.hint = hint
        self.sql = sql
        self.sql_dialect=sql_dialect
        self.db_id = db_id
        self.db_path = db_path

        self.key = "nl+schema+sql"
        
        self.test_cases = [
            QueryReviewTestCase(nl, hint, sql, sql_dialect, db_id, db_path),
            NLReviewTestCase(nl, hint, sql, sql_dialect, db_id, db_path)
        ]