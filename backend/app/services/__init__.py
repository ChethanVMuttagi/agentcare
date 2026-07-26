"""Services: business rules and transaction ownership above the repository layer.

Routes stay thin (parse/validate input, call a service, shape the
response); repositories stay dumb (persistence/query only). Services are
where the two meet, and where a mutating operation's transaction boundary
is decided — see docs/DATABASE.md "Transaction Ownership Philosophy".
"""
