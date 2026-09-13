from src.providers.news import NotAvailableNewsSentimentProvider


def test_stub_reports_not_available_and_invents_nothing():
    provider = NotAvailableNewsSentimentProvider()
    result = provider.get_sentiment("TCS.NS")

    assert result["symbol"] == "TCS.NS"
    assert result["status"] == "Not available in Phase 1"
    assert "sentiment_score" not in result
