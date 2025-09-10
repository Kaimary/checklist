import json
import re
import logging
from typing import Any, Dict, List, Tuple

from langchain_core.output_parsers.base import BaseOutputParser
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

class PythonListOutputParser(BaseOutputParser):
    """Parses output embedded in markdown code blocks containing Python lists."""
    
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def parse(self, output: str) -> Any:
        """
        Parses the output to extract Python list content from markdown.

        Args:
            output (str): The output string containing Python list.

        Returns:
            Any: The parsed Python list.
        """
        logging.debug(f"Parsing output with PythonListOutputParser: {output}")
        if "```python" in output:
            output = output.split("```python")[1].split("```")[0]
        output = re.sub(r"^\s+", "", output)
        return eval(output)  # Note: Using eval is potentially unsafe, consider using ast.literal_eval if possible.

class MedallionLayerDeterminationOutput(BaseModel):
    """Model for medallion layer dermination output."""
    chain_of_thought_reasoning: str = Field(description="One line explanation of why or why not the the schema is sufficient to the question.")
    is_evaluation_success: str = Field(description="Yes or No")

class ColumnFilteringOutput(BaseModel):
    """Model for column filtering output."""
    chain_of_thought_reasoning: str = Field(description="One line explanation of why or why not the column information is relevant to the question and the hint.")
    is_column_information_relevant: str = Field(description="Yes or No")

class TableSelectionOutputParser(BaseOutputParser):
    """Parses table selection outputs embedded in markdown code blocks containing JSON."""
    
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def parse(self, output: str) -> Any:
        """
        Parses the output to extract JSON content from markdown.

        Args:
            output (str): The output string containing JSON.

        Returns:
            Any: The parsed JSON content.
        """
        logging.debug(f"Parsing output with TableSelectionOutputParser: {output}")
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0]
        output = re.sub(r"^\s+", "", output)
        output = output.replace("\n", " ").replace("\t", " ")
        return json.loads(output)

class ColumnSelectionOutput(BaseModel):
    """Model for column selection output."""
    table_columns: Dict[str, Tuple[str, List[str]]] = Field(description="A mapping of table and column names to a tuple containing the reason for the column's selection and a list of keywords for data lookup. If no keywords are required, an empty list is provided.")

class SQLGenerationOutput(BaseModel):
    """Model for SQL generation output."""
    chain_of_thought_reasoning: str = Field(description="Your thought process on how you arrived at the final SQL query.")
    SQL: str = Field(description="The generated SQL query in a single string.")

class MarkDownOutputParser(BaseOutputParser):
    """Parses output embedded in markdown code blocks containing SQL queries."""
    
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def parse(self, output: str) -> Dict[str, str]:
        """
        Parses the output to extract SQL content from markdown.

        Args:
            output (str): The output string containing SQL query.

        Returns:
            Dict[str, str]: A dictionary with the SQL query.
        """
        logging.debug(f"Parsing output with MarkDownOutputParser: {output}")
        if "```sql" in output:
            output = output.split("```sql")[1].split("```")[0]
            output = re.sub(r"^\s+", "", output)
            return {"SQL": output}
        elif "```nl" in output:
            output = output.split("```nl")[1].split("```")[0]
            output = re.sub(r"^\s+", "", output)
            return {"NL": output}

class SQLRevisionOutput(BaseModel):
    """Model for SQL revision output."""
    chain_of_thought_reasoning: str = Field(description="Your thought process on how you arrived at the final SQL query.")

class OracleDataGenerationOutput(BaseModel):
    """Model for oracle data generation output."""
    chain_of_thought_reasoning: str = Field(description="Your thought process on how you think.")
    database_instances: Dict[str, Any] = Field(description="The generated data instances based on the database schema")
    resulting_data: Dict[str, Any] = Field(description="The expected resulting data queried by the given natural language over the given databse schema")

class OracleDataVerificationOutput(BaseModel):
    """Model for oracle data verification output."""
    chain_of_thought_reasoning: str = Field(description="Your thought process on how you think.")
    resulting_data: Dict[str, Any] = Field(description="The expected resulting data queried by the given natural language over the given databse schema")

class OracleResultCheckingOutput(BaseModel):
    """Model for oracle result checking output."""
    resulting_data: Dict[str, Any] = Field(description="The expected resulting data queried by the given natural language over the given databse schema")

class QueryRelaxingOutput(BaseModel):
    """Model for query relaxing constraint generation output."""
    chain_of_thought_reasoning: str = Field(description="Your thought process on how you think.")
    type: str = Field(description="Type for the relaxing (remove/relax)")
    description: str = Field(description="Brief description of the relaxing operation")
    nl_mutant: str = Field(description="the natural language mutant transformed from the orignal natural language after applying the relaxing")
    sql_mutant: str = Field(description="the query mutant transformed from the orignal SQL after applying the relaxing")

class SchemaPruningParser(BaseModel):
    """Model for schema pruning output."""
    pruned_schema: str = Field(description="The pruned database schema with only the necessary tables and columns.")

class LLMJudgmentOutput(BaseModel):
    """Model for LLM judgment output."""
    chain_of_thought_reasoning: str = Field(description="Your thought process on how you think.")
    judgment: str = Field(description="Yes or No")

def get_parser(parser_name: str) -> BaseOutputParser:
    """
    Returns the appropriate parser based on the provided parser name.

    Args:
        parser_name (str): The name of the parser to retrieve.

    Returns:
        BaseOutputParser: The appropriate parser instance.

    Raises:
        ValueError: If the parser name is invalid.
    """
    parser_configs = {
        "medallion_layer_determination": lambda: JsonOutputParser(pydantic_object=MedallionLayerDeterminationOutput),
        "keyword_extraction": PythonListOutputParser,
        "column_filtering": lambda: JsonOutputParser(pydantic_object=ColumnFilteringOutput),
        "table_selection": lambda: JsonOutputParser(pydantic_object=TableSelectionOutputParser),
        "column_selection": lambda: JsonOutputParser(pydantic_object=ColumnSelectionOutput),
        "candidate_generation": lambda: JsonOutputParser(pydantic_object=SQLGenerationOutput),
        "nl2sql_translation": MarkDownOutputParser,
        "revision": lambda: JsonOutputParser(pydantic_object=SQLRevisionOutput),
        "oracle_data_generation": lambda: JsonOutputParser(pydantic_object=OracleDataGenerationOutput),
        "oracle_data_verification": lambda: JsonOutputParser(pydantic_object=OracleDataVerificationOutput),
        "oracle_result_checking": lambda: JsonOutputParser(pydantic_object=OracleResultCheckingOutput),
        "nl_relaxing_generation": lambda: JsonOutputParser(pydantic_object=QueryRelaxingOutput),
        "nl_strengthening_generation": lambda: JsonOutputParser(pydantic_object=QueryRelaxingOutput),
        "nl_mutation_generation": MarkDownOutputParser,
        "llm_nl2sql_judgment": lambda: JsonOutputParser(pydantic_object=LLMJudgmentOutput),
        "schema_pruning": lambda: JsonOutputParser(pydantic_object=SchemaPruningParser),
    }

    if parser_name not in parser_configs:
        logging.error(f"Invalid parser name: {parser_name}")
        raise ValueError(f"Invalid parser name: {parser_name}")

    logging.info(f"Retrieving parser for: {parser_name}")
    parser = parser_configs[parser_name]() if callable(parser_configs[parser_name]) else parser_configs[parser_name]
    return parser
