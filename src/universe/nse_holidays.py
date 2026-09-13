# NSE trading holidays by year. Manually maintained — refresh every
# January from the official NSE holiday circular (nseindia.com). This 2026
# list was cross-verified on 2026-09-13 against two independent published
# calendars (Zerodha and Groww); it corrects an earlier approximation that
# had Holi and Ganesh Chaturthi on the wrong dates and a fabricated Diwali
# entry. VERIFY against the official NSE circular each January regardless —
# a regional one-off holiday (e.g. state elections) can also close trading
# and won't appear here until confirmed against the circular.
#
# 2026-08-15 (Independence Day) is not listed separately: it falls on a
# Saturday in 2026, so the weekday check alone already excludes it.
NSE_HOLIDAYS = {
    2026: [
        "2026-01-26",  # Republic Day
        "2026-03-03",  # Holi
        "2026-03-26",  # Shri Ram Navami
        "2026-03-31",  # Shri Mahavir Jayanti
        "2026-04-03",  # Good Friday
        "2026-04-14",  # Dr. Ambedkar Jayanti
        "2026-05-01",  # Maharashtra Day
        "2026-05-28",  # Bakri Eid
        "2026-06-26",  # Moharram
        "2026-09-14",  # Ganesh Chaturthi
        "2026-10-02",  # Gandhi Jayanti
        "2026-10-20",  # Dussehra
        "2026-11-10",  # Diwali-Balipratipada
        "2026-11-24",  # Guru Nanak Jayanti
        "2026-12-25",  # Christmas
    ],
}
