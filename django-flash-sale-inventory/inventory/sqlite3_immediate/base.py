from django.db.backends.sqlite3.base import DatabaseWrapper as SqliteDatabaseWrapper


class DatabaseWrapper(SqliteDatabaseWrapper):
    """sqlite backend that takes the write lock at BEGIN (BEGIN IMMEDIATE).

    Plain deferred BEGIN lets two read-then-write transactions deadlock on the
    lock upgrade ("database table is locked"); IMMEDIATE serializes writers.
    """

    def _start_transaction_under_autocommit(self):
        self.cursor().execute("BEGIN IMMEDIATE")
