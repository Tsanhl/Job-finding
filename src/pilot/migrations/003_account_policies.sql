CREATE TABLE account_policies(employer TEXT NOT NULL,origin TEXT NOT NULL,email TEXT NOT NULL,mailbox TEXT NOT NULL,rules TEXT NOT NULL CHECK(json_valid(rules)),PRIMARY KEY(employer,origin,email));
