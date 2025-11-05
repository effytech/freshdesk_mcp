import unittest
import os
import sys
import asyncio

# Add the parent directory to the path so we can import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.freshdesk_mcp.server import parse_link_header


# Create a mock version of our server functions to isolate testing
async def mock_list_companies(page=1, per_page=30):
    companies = [
        {
            "id": 51000641139,
            "name": "Herbert Smith Freehills",
            "description": None,
            "note": None,
            "domains": ["herbertsmithfreehills.com"],
            "created_at": "2022-04-25T22:57:04Z",
            "updated_at": "2024-03-20T00:25:29Z",
            "custom_fields": {
                "organisation_name": "Herbert Smith Freehills",
                "account_status": "Active",
                "hosting_platform": "Acquia",
            },
        },
        {
            "id": 51000979809,
            "name": "Another Company",
            "domains": [],
            "created_at": "2023-05-15T10:30:00Z",
            "updated_at": "2024-01-10T15:45:22Z",
            "custom_fields": {
                "organisation_name": "Another Org",
                "account_status": "Active",
            },
        },
    ]

    pagination_info = {
        "next": 2 if page < 3 else None,
        "prev": page - 1 if page > 1 else None,
    }

    return {
        "companies": companies,
        "pagination": {
            "current_page": page,
            "next_page": pagination_info.get("next"),
            "prev_page": pagination_info.get("prev"),
            "per_page": per_page,
        },
    }


async def mock_view_company(company_id):
    if company_id == 51000641139:
        return {
            "id": 51000641139,
            "name": "Herbert Smith Freehills",
            "description": None,
            "note": None,
            "domains": ["herbertsmithfreehills.com"],
            "created_at": "2022-04-25T22:57:04Z",
            "updated_at": "2024-03-20T00:25:29Z",
            "custom_fields": {
                "organisation_name": "Herbert Smith Freehills",
                "account_status": "Active",
                "hosting_platform": "Acquia",
            },
        }
    else:
        return {"error": "Company not found"}


async def mock_search_companies(query):
    if "herbert" in query.lower():
        return [
            {"id": 51000641139, "name": "Herbert Smith Freehills"},
            {"id": 51000979809, "name": "Another Herbert Company"},
        ]
    else:
        return []


async def mock_list_company_fields():
    return [
        {
            "id": 51000152653,
            "name": "name",
            "label": "Company Name",
            "position": 1,
            "required_for_agents": True,
            "type": "default_name",
            "default": True,
        },
        {
            "id": 51000169767,
            "name": "organisation_name",
            "label": "Organisation Name",
            "position": 2,
            "required_for_agents": True,
            "type": "custom_text",
            "default": False,
        },
        {
            "id": 51000265522,
            "name": "account_status",
            "label": "Account Status",
            "position": 3,
            "required_for_agents": False,
            "type": "custom_dropdown",
            "default": False,
            "choices": ["Active", "Expired"],
        },
    ]


# Class for sync tests using unittest
class TestParseHeaderFunction(unittest.TestCase):
    def test_parse_link_header(self):
        # Test the parse_link_header function directly
        header = '<https://example.com/page=2>; rel="next", <https://example.com/page=1>; rel="prev"'
        result = parse_link_header(header)
        self.assertEqual(result.get("next"), 2)
        self.assertEqual(result.get("prev"), 1)

    def test_parse_link_header_empty(self):
        # Test with empty header
        result = parse_link_header("")
        self.assertEqual(result, {"next": None, "prev": None})

    def test_parse_link_header_invalid_format(self):
        # Test with invalid format
        result = parse_link_header("invalid format")
        self.assertEqual(result, {"next": None, "prev": None})


# Define async test cases outside of unittest framework
async def test_list_companies():
    result = await mock_list_companies(page=1, per_page=10)

    assert "companies" in result
    assert len(result["companies"]) == 2
    assert result["companies"][0]["name"] == "Herbert Smith Freehills"
    assert "pagination" in result
    assert result["pagination"]["current_page"] == 1
    assert "next_page" in result["pagination"]
    print("✓ test_list_companies passed")


async def test_view_company():
    result = await mock_view_company(51000641139)

    assert result["id"] == 51000641139
    assert result["name"] == "Herbert Smith Freehills"
    assert result["domains"] == ["herbertsmithfreehills.com"]
    print("✓ test_view_company passed")


async def test_search_companies():
    result = await mock_search_companies("herbert")

    assert len(result) == 2
    assert result[0]["id"] == 51000641139
    assert result[0]["name"] == "Herbert Smith Freehills"
    print("✓ test_search_companies passed")


async def test_list_company_fields():
    result = await mock_list_company_fields()

    assert len(result) == 3
    assert result[0]["name"] == "name"
    assert result[1]["name"] == "organisation_name"
    assert result[2]["name"] == "account_status"
    print("✓ test_list_company_fields passed")


if __name__ == "__main__":
    # Run async tests
    print("Running async tests:")
    asyncio.run(test_list_companies())
    asyncio.run(test_view_company())
    asyncio.run(test_search_companies())
    asyncio.run(test_list_company_fields())

    # Run sync tests
    print("\nRunning sync tests:")
    unittest.main(argv=["first-arg-is-ignored"], exit=False)
