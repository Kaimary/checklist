from colorama import Fore
from camel.societies import RolePlaying
from camel.utils import print_text_animated

import os

from camel.models import ModelFactory
from camel.types import ModelPlatformType, ModelType
from camel.configs import ChatGPTConfig

# sys_msg = 'You are a curious stone wondering about the universe.'

# # Define the model, here in this case we use gpt-4o-mini
# model = ModelFactory.create(
#     model_platform=ModelPlatformType.AZURE,
#     model_type=ModelType.GPT_4O_MINI,
#     model_config_dict=ChatGPTConfig().as_dict(), # [Optional] the config for model
# )
# from camel.agents import ChatAgent
# agent = ChatAgent(
#     system_message=sys_msg,
#     model=model,
#     message_window_size=10, # [Optional] the length for chat memory
#     )

# # Define a user message
# usr_msg = 'what is information in your mind?'

# # Sending the message to the agent
# response = agent.step(usr_msg)

# # Check the response (just for illustrative purpose)
# print(response.msgs[0].content)
def main(model=None, chat_turn_limit=50) -> None:
  task_prompt = ("Using Rubber Duck Debugging to verify the correctness of the SQL query clause by clause\n"
                 "SELECT c.country, SUM(o.amount) as total_sales\n"
                 "FROM customers c\n"
                 "JOIN orders o ON c.id = o.customer_id\n"
                 "GROUP BY c.country;\n"
                 "for natural language question \"Get the total sales per country for customers who made orders in 2023.\" under the database schema\n"
                 "- customers(id, name, country)\n"
                 "- orders(id, customer_id, order_date, amount)\n\n"
                 )
#   task_prompt = (
#     "The SQL Developer wants to verify if their SQL query correctly implements the intended logic. "
#     "The Rubber Duck Debugging Assistant's job is to ask clarifying questions, listen carefully, "
#     "and guide the developer to reason through the SQL step by step, without directly providing answers."
#     "\n\n"
#     "Here is the natural language task description:\n"
#     "\"Get the total sales per country for customers who made orders in 2023.\"\n\n"
#     "Here is the database schema:\n"
#     "- customers(id, name, country)\n"
#     "- orders(id, customer_id, order_date, amount)\n\n"
#     "Here is the SQL query the developer wrote:\n"
#     "SELECT c.country, SUM(o.amount) as total_sales\n"
#     "FROM customers c\n"
#     "JOIN orders o ON c.id = o.customer_id\n"
#     "WHERE YEAR(o.order_date) = 2023\n"
#     "GROUP BY c.country;\n\n"
#     "The conversation starts with the developer explaining the query, and the duck asks clarifying questions "
#     "to help debug and verify correctness."
#   )
  role_play_session = RolePlaying(
      assistant_role_name="SQL Developer",
      assistant_agent_kwargs=dict(model=model),
      user_role_name="Rubber Duck Debugging Assistant",
      user_agent_kwargs=dict(model=model),
      task_prompt=task_prompt,
      with_task_specify=False,
    #   task_specify_agent_kwargs=dict(model=model),
  )

  # Print initial system messages
  print(
      Fore.GREEN
      + f"AI Assistant sys message:\\n{role_play_session.assistant_sys_msg}\\n"
  )
  print(
      Fore.BLUE + f"AI User sys message:\\n{role_play_session.user_sys_msg}\\n"
  )

  print(Fore.YELLOW + f"Original task prompt:\\n{task_prompt}\\n")
  print(
      Fore.CYAN
      + "Specified task prompt:"
      + f"\\n{role_play_session.specified_task_prompt}\\n"
  )
  print(Fore.RED + f"Final task prompt:\\n{role_play_session.task_prompt}\\n")

  n = 0
  input_msg = role_play_session.init_chat()

  # Turn-based simulation
  while n < chat_turn_limit:
      n += 1
      assistant_response, user_response = role_play_session.step(input_msg)

      if assistant_response.terminated:
          print(
              Fore.GREEN
              + (
                  "AI Assistant terminated. Reason: "
                  f"{assistant_response.info['termination_reasons']}."
              )
          )
          break
      if user_response.terminated:
          print(
              Fore.GREEN
              + (
                  "AI User terminated. "
                  f"Reason: {user_response.info['termination_reasons']}."
              )
          )
          break

      print_text_animated(
          Fore.BLUE + f"AI User:\\n\\n{user_response.msg.content}\\n"
      )
      print_text_animated(
          Fore.GREEN + "AI Assistant:\\n\\n"
          f"{assistant_response.msg.content}\\n"
      )

      if "CAMEL_TASK_DONE" in user_response.msg.content:
          break

      input_msg = assistant_response.msg

if __name__ == "__main__":
  model = ModelFactory.create(
     model_platform=ModelPlatformType.AZURE,
     model_type=ModelType.GPT_4O_MINI,
     model_config_dict=ChatGPTConfig().as_dict(), # [Optional] the config for model
    )
  main(model)