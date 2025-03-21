#!/usr/bin/env python3
import mysql.connector
import json
import bcrypt

class Auth:
    """
    A class responsible for authenticating users,
    storing their roles/groups, and checking permissions.
    """

    def __init__(self, db_host, db_user, db_password, db_name):
        """
        Initialize DB connection parameters.
        In a more robust system, you'd handle connection pooling or pass a connection around.
        """
        self.db_host = db_host
        self.db_user = db_user
        self.db_password = db_password
        self.db_name = db_name

        # These attributes hold state for the currently logged-in user.
        self.user_id = None         # e.g. int user ID
        self.username = None        # string
        self.groups = set()         # set of group IDs
        self.roles = set()          # set of role IDs
        self.permissions = {}       # dict: {perm_name: max_level, ...}

    def login_token(self, token: str) -> bool:
        """
        Attempt to log in the user.
        1) Verify credentials (users table).
        2) Load username, user_id, groups, roles, and merge permissions.
        3) Return True if successful, False otherwise.
        """
        # 1) Verify credentials in the DB
        if self._verify_token(token):

            # 2) Load groups (including default groups)
            self.groups = self._fetch_user_groups()

            # 3) Load roles (including default roles and those from groups)
            self.roles = self._fetch_user_roles(self.groups)

            # 4) Merge permissions
            self.permissions = self._merge_permissions(self.roles)

            return True

        return False

    def login_basic(self, username: str, password: str) -> bool:
        """
        Attempt to log in the user.
        1) Verify credentials (users table).
        2) Load user_id, groups, roles, and merge permissions.
        3) Return True if successful, False otherwise.
        """
        # 1) Verify credentials in the DB
        if self._verify_credentials(username, password):

            # 2) Load groups (including default groups)
            self.groups = self._fetch_user_groups()

            # 3) Load roles (including default roles and those from groups)
            self.roles = self._fetch_user_roles(self.groups)

            # 4) Merge permissions
            self.permissions = self._merge_permissions(self.roles)

            return True

        return False

    def logout(self):
        """
        Clears the current logged-in user data.
        """
        self.user_id = None
        self.username = None
        self.groups.clear()
        self.roles.clear()
        self.permissions.clear()

    def is_logged_in(self) -> bool:
        """
        Returns True if a user is currently logged in, else False.
        """
        return self.user_id is not None

    def has_permission(self, perm_key: str, min_level: int = 1) -> bool:
        """
        Check if the current user has at least `min_level` for `perm_key`.
        For example, min_level=4 might be 'admin' level.
        """
        current_level = self.permissions.get(perm_key, 0)
        return current_level >= min_level

    # --------------------------------------------------------------------------
    # Internals
    # --------------------------------------------------------------------------

    def _verify_token(self, token: str):
        """
        1. Look up token in `tokens` table by token string.
        2. Check if token has expired (if 'expires' is not null).
        3. Look up the user in `users` table by the token's 'user' field.
        4. If user is found and token is valid, store user data in self and return True,
           otherwise return False.
        """
        conn = mysql.connector.connect(
            host=self.db_host,
            user=self.db_user,
            password=self.db_password,
            database=self.db_name
        )
        try:
            cursor = conn.cursor(dictionary=True)

            sql = "SELECT id, token, expires, user FROM tokens WHERE token = %s"
            cursor.execute(sql, (token,))
            token_row = cursor.fetchone()

            if not token_row:
                return False

            # 1) Check expiration if 'expires' is not null.
            if token_row["expires"] is not None:
                token_expires = token_row["expires"]
                # If 'expires' is a datetime and the current time is past that, token is invalid.
                if datetime.datetime.now() > token_expires:
                    return False

            # 2) Look up the user
            user_id = token_row["user"]
            if not user_id:
                return False

            sql_user = "SELECT id, username, backend FROM users WHERE id = %s"
            cursor.execute(sql_user, (token_row["user"],))
            user_row = cursor.fetchone()

            if not user_row:
                return False

            self.username = user_row["username"]
            self.user_id = user_row["id"]

            return True
        finally:
            conn.close()

    def _verify_credentials(self, username: str, plaintext_password: str):
        """
        1. Look up user in `users` table by username.
        2. Compare hashed password using bcrypt.
        3. Return True if valid, otherwise False.
        """
        conn = mysql.connector.connect(
            host=self.db_host,
            user=self.db_user,
            password=self.db_password,
            database=self.db_name
        )
        try:
            cursor = conn.cursor(dictionary=True)
            sql = "SELECT id, username, backend FROM users WHERE username = %s"
            cursor.execute(sql, (username,))
            user_row = cursor.fetchone()

            if not user_row:
                return False

            self.username = user_row["username"]
            self.user_id = user_row["id"]
            backend_id = user_row["backend"]

            # Now fetch the hashed password from the `backends` table
            sql_backend = "SELECT password FROM backends WHERE id = %s"
            cursor.execute(sql_backend, (backend_id,))
            row_backend = cursor.fetchone()
            if not row_backend:
                return False

            stored_hash = row_backend["password"]
            if not stored_hash:
                return False

            # Verify bcrypt
            if bcrypt.checkpw(plaintext_password.encode('utf-8'), stored_hash.encode('utf-8')):
                return True
            return False
        finally:
            conn.close()

    def _fetch_user_groups(self):
        """
        Return a set of group IDs for this user.
        1) Include groups with isDefault=1
        2) Include groups whose `users` JSON contains this user
        """
        user_group_ids = set()

        conn = mysql.connector.connect(
            host=self.db_host,
            user=self.db_user,
            password=self.db_password,
            database=self.db_name
        )
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, users, isDefault FROM groups")
            groups_rows = cursor.fetchall()
            for row in groups_rows:
                group_id = row["id"]
                is_default = row["isDefault"]
                users_json = row["users"]

                # If isDefault=1 -> user is in that group
                if is_default == 1:
                    user_group_ids.add(group_id)
                else:
                    # If the user is listed in that group's JSON
                    if users_json:
                        members = json.loads(users_json)  # e.g. [1,2,3] or ["alice","bob"]
                        # Depending on how you store user_id, might need str(user_id)
                        if self.user_id in members:
                            user_group_ids.add(group_id)

            return user_group_ids
        finally:
            conn.close()

    def _fetch_user_roles(self, user_group_ids: set):
        """
        Return a set of role IDs for this user.
        1) Roles with isDefault=1
        2) Roles whose `users` JSON includes this user
        3) Roles whose `groups` JSON intersects user_group_ids
        """
        user_role_ids = set()

        conn = mysql.connector.connect(
            host=self.db_host,
            user=self.db_user,
            password=self.db_password,
            database=self.db_name
        )
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, users, groups, isDefault FROM roles")
            roles_rows = cursor.fetchall()

            for row in roles_rows:
                role_id = row["id"]
                is_default = row["isDefault"]
                role_users_json = row["users"]
                role_groups_json = row["groups"]

                belongs = False
                if is_default == 1:
                    belongs = True
                else:
                    # Check user membership
                    if role_users_json:
                        role_users = json.loads(role_users_json)
                        if self.user_id in role_users:
                            belongs = True
                    # Check group membership
                    if not belongs and role_groups_json:
                        role_groups = json.loads(role_groups_json)
                        # If there's an intersection
                        if set(role_groups).intersection(user_group_ids):
                            belongs = True

                if belongs:
                    user_role_ids.add(role_id)

            return user_role_ids
        finally:
            conn.close()

    def _merge_permissions(self, role_ids: set):
        """
        Given a set of role IDs, fetch their `permissions` JSON,
        parse them, and combine them (taking the max level or union).
        Returns a dict: { 'KICK': 4, 'STOP': 4, 'READ': 2, etc. }
        """
        aggregated = {}
        if not role_ids:
            return aggregated

        # Construct a simple IN clause
        placeholders = ", ".join(["%s"] * len(role_ids))  # e.g. "%s, %s, %s"
        sql = f"SELECT permissions FROM roles WHERE id IN ({placeholders})"

        conn = mysql.connector.connect(
            host=self.db_host,
            user=self.db_user,
            password=self.db_password,
            database=self.db_name
        )
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, tuple(role_ids))
            rows = cursor.fetchall()

            for row in rows:
                perm_json = row["permissions"]
                if not perm_json:
                    continue
                role_perms = json.loads(perm_json)
                for k, v in role_perms.items():
                    # Merge logic: store max
                    if k not in aggregated:
                        aggregated[k] = v
                    else:
                        aggregated[k] = max(aggregated[k], v)
        finally:
            conn.close()

        return aggregated
