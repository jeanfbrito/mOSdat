"""Transport-level helpers: failover error classification."""


def _is_failover_error(exc: Exception) -> bool:
    """Return True if the exception warrants failover to the next endpoint.

    Retryable: connection errors, 5xx, 429 rate-limit, timeout.
    Non-retryable: 4xx (except 429), parse errors, schema errors.
    """
    import httpx
    from openai import APIConnectionError, APITimeoutError, RateLimitError
    from openai import APIStatusError

    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, RateLimitError):  # 429
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500  # 5xx only
    # httpx transport-level errors
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)):
        return True
    return False
