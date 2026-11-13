BEGIN;
-- IG Connect "Save Account": one Instagram account per studio (fix B, 2026-11-08).
-- Uses the live rows: studio 2 has active account 17841414365226685, studio 4 has
-- 17841467196796493, studio 1 has none. Everything is rolled back.
PREPARE save(text, int, text, text, text, int) AS {{QUERY:ig-connect.json:Save Account}};
\echo 1) second account on studio 2 -> studio_has_account, names the existing account, link NOT used:
EXECUTE save('sqltest-n1', 2, 'sqltest-new', 'new_acct', 'AQID', 5184000);
SELECT count(*) AS link_rows_for_n1 FROM ig_connect_uses WHERE nonce = 'sqltest-n1';
\echo 2) the same account reconnecting to its own studio -> connected:
EXECUTE save('sqltest-n2', 2, '17841414365226685', 'looplogicsystems_dev_test', 'AQID', 5184000);
\echo 3) the studio 4 account onto studio 2 -> other_studio:
EXECUTE save('sqltest-n3', 2, '17841467196796493', 'looplogic_dev_test_2', 'AQID', 5184000);
\echo 4) a studio with no account yet -> connected:
EXECUTE save('sqltest-n4', 1, 'sqltest-s1', 's1_acct', 'AQID', 5184000);
\echo 5) the studio 2 account switched off first, then a new one -> connected:
UPDATE channel_accounts SET active = false WHERE studio_id = 2 AND channel = 'instagram';
EXECUTE save('sqltest-n5', 2, 'sqltest-new2', 'new_acct2', 'AQID', 5184000);
\echo 6) a link already used, same account again -> link_used:
EXECUTE save('sqltest-n5', 2, 'sqltest-new2', 'new_acct2', 'AQID', 5184000);
\echo 7) a hand-written second active Instagram row on studio 2 -> refused by the database itself (migration 018):
DO $$ BEGIN
  INSERT INTO channel_accounts (studio_id, channel, account_ref, active) VALUES (2, 'instagram', 'sqltest-hand', true);
  RAISE NOTICE 'NOT refused: is migration 018 applied?';
EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'refused (one active Instagram account per studio)';
END $$;
ROLLBACK;
