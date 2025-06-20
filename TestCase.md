## Syntax Testing
Check whether the SQL query is executable or not. (Minimum Requirement Test)

### Unit Tests
> Executable(**SQL**, **Schema**) == True

## Semantics Testing
Check if the SQL query contains any semantics errors (_ref._ REDSQL) by examining its query plan along with the immediate "executed".

### Unit Tests
Define a set of **constraints** to maintain semantics _correctness_ of query.
1. **Wrong JOIN**: There must be overlapping values between _two or more tables_:
> JOIN(**Table1**, **Table2**) $\neq$ $\empty$
2. **Wrong Calculation/Comparison**: Operations between _columns_ must involve compatible data types:
> Type(**Column1**, **Column2**) == compatable
3. **Predicates Inconsistent**: There must exist a tuple that satisfies a given predicate in SQL:
> Output(**Predicate**) $\neq$ $\empty$
4. **Uncertain Projection/HAVING**: For all tuples have the same _column1_ (used in GPOUP BY) value, they have the same _column2_ (used in SELECT or HAVING) value:
> ValuesEq(**column1**, **column2**) == True
5. **Idle GROUP BY**: There must exist at least two tuples that have the same value for column1 (used in GROUP BY):
> Distinct(**column1**) < Count(**column1**)

## Execution Consistency Testing
For a given database instance, an NL to SQL mapping satisfies **execution consistency** if the execution result of an NL query matches that of the corresponding SQL.

### Unit Tests
Generate multiple SQL candidates and use **SQL equivalence checker** to obtain **counterexample database instances** that can distinguish non-equivalent SQLs candidate, then use the couterexamples to check the consistency between SQL and NL:

> Output(**NL**, **counterexamples1**) == Output(**SQL**, **counterexamples1**)
> Output(**NL**, **counterexamples2**) == Output(**SQL**, **counterexamples2**)
> ...

### Test Fixture
- **SQL candidates** == LLM1(**NL** + **Schema**) + LLM2(**NL** + **Schema**) + ...
- **Counterexamples** == EquivalenceChecker(**SQL**, **SQL candidates**)

## Differential Testing
Generate **multiple SQLs** from different models and _consistent results_ across different **generators** (_models_) suggest correctness.

### Unit Tests
> **SQL** == Vote(**SQLs**)

### Test Fixture
- **SQLs** == LLM1(**NL** + **Schema**) + LLM2(**NL** + **Schema**) + ...

## Metamorphic Testing
Modify **inputs** (_database_ or _NL_ (reflected on SQL)) in a controlled way, to check if the relationships between inputs and **outputs** (_executed results_) hold.

### Unit Tests
Define a set of **metamorphic relations** (MRs), i.e., invariant properties that remain true when the input is changed in a specific way:

1. **Query relaxation**
If an NL is relaxed (e.g., "employees with salary > 50000" → "employees with salary > 40000"), the result set should contain all original results plus possibly more:
> **SQL1** == Map(Relax(**NL**))
> Output(**SQL**) $\in$ Output(**SQL1**)

2. **Query strengthening**
If the query is made stricter, the result set should be a subset of the original:
> **SQL1** == Map(Strengthen(**NL**))
> Output(**SQL1**) $\in$ Output(**SQL**)

3. **Additive Test Data Injection**
Add a row to the database that satisfies the NL condition, it must appear in the query result:
> **Data1** == Add(**Data**)
> **Data1** $\in$ Output(**SQL1** + **Database**)


## Oracle-Based Testing
Generate **test oracles** (_executed results_) using **constraints** (_NL_ and _schema_), and use oracles to test.

### Unit Tests
Using the knowledge of the NL and schema, generate unit tests: 
Generate database instances (_a.k.a._ data) where the expected result of the NL is easily predictable. If the executed results of SQL doesn’t match expectations, the translation is suspect.

> Output<**Tested SQL** + **Data1**> == **Result1**
> Output<**Tested SQL** + **Data2**> == **Result2**
> ...

### Test Fixture
- **Data** == Generator(**NL** + **DB Schema**)
- **Result** == LLM(**NL**, **Data**)