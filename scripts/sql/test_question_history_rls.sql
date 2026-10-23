-- Test: Row Level Security on public.question_history
-- ====================================================
-- Paste this whole file into the Supabase SQL editor (or run it with psql)
-- AFTER running scripts/01_create_schema.py. Nothing persists: everything
-- happens inside one transaction that ends with ROLLBACK, including the two
-- throwaway users.
--
-- What it checks (the final SELECT prints one PASS/FAIL line per check):
--   1. user A sees only A's rows, user B sees only B's rows
--   2. A cannot insert a row owned by B (user_id is not in the column grant)
--   3. ...and even if user_id WERE granted, the WITH CHECK policy blocks it
--   4. A cannot UPDATE or DELETE (no privilege)
--   5. anon cannot read the table at all
--
-- The "must be denied" checks run inside DO blocks with an EXCEPTION handler
-- (a savepoint under the hood) instead of bare statements: the SQL editor
-- sends the script as one request and stops at the first error, so an
-- expected error would abort the run. A DO block catches it and records it.

BEGIN;

-- A scratch table for results. It is temporary, so it vanishes with the
-- transaction, and every role may write to it.
CREATE TEMP TABLE rls_results (
    n        int,
    label    text,
    expected text,
    actual   text
);
GRANT ALL ON rls_results TO anon, authenticated;

-- Two throwaway users. auth.users has defaults for everything but id (the
-- email just makes them easy to recognise if the rollback ever fails).
INSERT INTO auth.users (id, email) VALUES
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'rls-test-a@example.invalid'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'rls-test-b@example.invalid');

-- --------------------------------------------------------------------
-- User A: becomes the "authenticated" role with A's JWT, writes one row.
-- Supabase's auth.uid() reads request.jwt.claim.sub or request.jwt.claims,
-- depending on version, so both are set. The third argument (true) makes
-- the setting local to this transaction.
-- --------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', true);
SELECT set_config('request.jwt.claims',
    json_build_object('sub', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'role', 'authenticated')::text, true);

-- user_id is not supplied: it comes from its DEFAULT auth.uid()
INSERT INTO public.question_history (question, answer)
VALUES ('A: question', 'A: answer');

RESET ROLE;

-- --------------------------------------------------------------------
-- User B: same thing with B's identity.
-- --------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', true);
SELECT set_config('request.jwt.claims',
    json_build_object('sub', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'role', 'authenticated')::text, true);

INSERT INTO public.question_history (question, answer)
VALUES ('B: question', 'B: answer');

-- B's view: only B's row
INSERT INTO rls_results
SELECT 2, 'B sees (count)', '1', count(*)::text FROM public.question_history;
INSERT INTO rls_results
SELECT 3, 'B sees (questions)', 'B: question', coalesce(string_agg(question, ', ' ORDER BY question), '')
FROM public.question_history;

RESET ROLE;

-- --------------------------------------------------------------------
-- Back to user A: reads, then tries things A must not be able to do.
-- --------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', true);
SELECT set_config('request.jwt.claims',
    json_build_object('sub', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'role', 'authenticated')::text, true);

-- A's view: only A's row
INSERT INTO rls_results
SELECT 0, 'A sees (count)', '1', count(*)::text FROM public.question_history;
INSERT INTO rls_results
SELECT 1, 'A sees (questions)', 'A: question', coalesce(string_agg(question, ', ' ORDER BY question), '')
FROM public.question_history;

-- A tries to insert a row owned by B. user_id is not in the column grant,
-- so Postgres refuses before RLS is even consulted (SQLSTATE 42501).
DO $$
BEGIN
    INSERT INTO public.question_history (user_id, question, answer)
    VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'forged', 'forged');
    INSERT INTO rls_results VALUES (4, 'A inserts row owned by B', 'permission denied', 'INSERT SUCCEEDED');
EXCEPTION WHEN insufficient_privilege THEN
    INSERT INTO rls_results VALUES (4, 'A inserts row owned by B', 'permission denied', 'permission denied');
END $$;

-- A cannot change or remove history (no UPDATE / DELETE privilege)
DO $$
BEGIN
    UPDATE public.question_history SET answer = 'tampered';
    INSERT INTO rls_results VALUES (6, 'A updates a row', 'permission denied', 'UPDATE SUCCEEDED');
EXCEPTION WHEN insufficient_privilege THEN
    INSERT INTO rls_results VALUES (6, 'A updates a row', 'permission denied', 'permission denied');
END $$;
DO $$
BEGIN
    DELETE FROM public.question_history;
    INSERT INTO rls_results VALUES (7, 'A deletes rows', 'permission denied', 'DELETE SUCCEEDED');
EXCEPTION WHEN insufficient_privilege THEN
    INSERT INTO rls_results VALUES (7, 'A deletes rows', 'permission denied', 'permission denied');
END $$;

RESET ROLE;

-- --------------------------------------------------------------------
-- Second layer: pretend a future change granted INSERT on user_id. (This
-- GRANT runs as the table owner and is rolled back with everything else.)
-- The WITH CHECK policy must still refuse a row owned by someone else.
-- --------------------------------------------------------------------
GRANT INSERT (user_id) ON public.question_history TO authenticated;

SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', true);
SELECT set_config('request.jwt.claims',
    json_build_object('sub', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'role', 'authenticated')::text, true);

DO $$
BEGIN
    INSERT INTO public.question_history (user_id, question, answer)
    VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'forged', 'forged');
    INSERT INTO rls_results VALUES (5, 'A inserts as B (if user_id were granted)', 'blocked by RLS', 'INSERT SUCCEEDED');
EXCEPTION WHEN insufficient_privilege THEN
    -- SQLSTATE 42501 is also what a WITH CHECK failure raises
    INSERT INTO rls_results VALUES (5, 'A inserts as B (if user_id were granted)', 'blocked by RLS', 'blocked by RLS');
END $$;

RESET ROLE;

-- --------------------------------------------------------------------
-- anon (not signed in): has no privileges on the table at all.
-- --------------------------------------------------------------------
SET LOCAL ROLE anon;
SELECT set_config('request.jwt.claim.sub', '', true);
SELECT set_config('request.jwt.claims', json_build_object('role', 'anon')::text, true);

DO $$
DECLARE
    c bigint;
BEGIN
    SELECT count(*) INTO c FROM public.question_history;
    INSERT INTO rls_results VALUES (8, 'anon reads the table', 'permission denied', 'saw ' || c || ' rows');
EXCEPTION WHEN insufficient_privilege THEN
    INSERT INTO rls_results VALUES (8, 'anon reads the table', 'permission denied', 'permission denied');
END $$;

RESET ROLE;

-- --------------------------------------------------------------------
-- Results: one line per check, in order. Expect all PASS.
-- --------------------------------------------------------------------
SELECT n,
       label,
       expected,
       actual,
       CASE WHEN expected = actual THEN 'PASS' ELSE 'FAIL' END AS result
FROM rls_results
ORDER BY n;

-- Undo everything: the test users, their rows, the temp GRANT, the scratch
-- table. (If the editor only shows the last statement's result, comment out
-- this line, run, read the table, then run ROLLBACK; on its own.)
ROLLBACK;
