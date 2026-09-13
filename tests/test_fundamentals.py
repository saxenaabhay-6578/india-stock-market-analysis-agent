from src.providers.fundamentals import NotAvailableFundamentalsProvider


def test_stub_reports_not_available_and_invents_nothing():
    provider = NotAvailableFundamentalsProvider()
    result = provider.get_fundamentals("TCS.NS")

    assert result["symbol"] == "TCS.NS"
    assert result["status"] == "Not available in Phase 1"
    assert "pe_ratio" not in result
    assert "roe" not in result
