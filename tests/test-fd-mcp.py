import asyncio
from freshdesk_mcp.server import get_ticket, update_ticket, get_ticket_conversation, update_ticket_conversation,get_agents, list_canned_responses, list_solution_articles, list_solution_categories,list_solution_folders

async def test_get_ticket():
    ticket_id = "1289" #Replace with a test ticket Id
    result = await get_ticket(ticket_id)
    print(result)


async def test_update_ticket():
    ticket_id = 1289 #Replace with a test ticket Id 
    ticket_fields = {"status": 5}
    result = await update_ticket(ticket_id, ticket_fields)
    print(result)

async def test_get_ticket_conversation():
    ticket_id = 1294 #Replace with a test ticket Id 
    result = await get_ticket_conversation(ticket_id)
    print(result)

async def test_update_ticket_conversation():
    conversation_id = 60241927935 #Replace with a test conversation Id 
    body = "This is a test reply"
    result = await update_ticket_conversation(conversation_id, body)
    print(result)

async def test_get_agents():
    page = 1
    per_page = 30
    result = await get_agents(page, per_page)
    print(result)

async def test_list_canned_responses():
    result = await list_canned_responses()
    print(result)

async def test_list_solution_articles():
    result = await list_solution_articles()
    print(result)

async def test_list_solution_folders():
    category_id = 60000237037
    result = await list_solution_folders(category_id)
    print(result)

async def test_list_solution_categories():
    result = await list_solution_categories()
    print(result)

async def test_list_solution_articles():
    folder_id = 60000347598
    result = await list_solution_articles(folder_id)
    print(result)

if __name__ == "__main__":
    # asyncio.run(test_get_ticket())
    # asyncio.run(test_update_ticket())
    # asyncio.run(test_get_ticket_conversation())
    # asyncio.run(test_update_ticket_conversation())
    # asyncio.run(test_get_agents())
    # asyncio.run(test_list_canned_responses())
    # asyncio.run(test_list_solution_articles())
    # asyncio.run(test_list_solution_folders())
    asyncio.run(test_list_solution_categories())