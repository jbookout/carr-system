-- V5-J103 governed correspondence store: transaction-scoped PostgreSQL proof.
--
-- HOW TO RUN, exactly:
--   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f mcp-server/test/governed-correspondence-store-postgres.sql
-- on a database migrated through the governed correspondence store migration,
-- as the schema owner (the local-db-ci migration lane runs it that way).
--
-- EVERYTHING IS ROLLED BACK. No row survives, no role or actor is created, no
-- provider or network is touched. The seeded actors joe, dell and automation
-- (migration 0002) are used as they are.
--
-- WHAT IT PROVES, none of which reading the SQL can show:
--   * consent is read-only by CHECK: send_mail_message and every other F10 write
--     operation is refused, and one partner cannot consent for the other's mailbox
--   * a read receipt carries partner, account and native provenance copied from
--     the consent, refuses raw content and routable addresses, and admits only
--     correspondence classified as related
--   * a draft can never be dispatchable or skip the human send, can never hold a
--     routable address or a dialable number, and cannot exist without a receipt
--     whose consent is still in force and whose partner sponsors the transaction
--   * every relation is append-only and no runtime role holds DML or the
--     receipt writer
--   * no correspondence relation has a column a destination, send instruction or
--     sent status could live in

\set ON_ERROR_STOP on
begin;

-- The relation shapes: no destination or send column anywhere.
do $$
declare v_bad text;
begin
  select string_agg(table_name || '.' || column_name, ', ') into v_bad
    from information_schema.columns
   where table_schema = 'ops' and table_name like 'correspondence\_%'
     and (column_name ~ '(recipient|destination|outbound|provider_operation|to_address|reply_to|smtp|schedule)'
          or column_name ~ '(^|_)(sent|status|send_at|dispatch_at|delivered)(_|$)'
          or (column_name ~ '(send|dispatch)' and column_name not in ('requires_human_send', 'dispatchable')));
  if v_bad is not null then
    raise exception 'correspondence relations carry dispatch-shaped columns: %', v_bad;
  end if;
end $$;

-- No runtime role holds DML, and nobody holds the receipt writer.
do $$
declare r text; t text;
begin
  foreach r in array array['carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority'] loop
    foreach t in array array['ops.correspondence_adapter_consent', 'ops.correspondence_adapter_consent_revocation',
                             'ops.correspondence_adapter_read_receipt', 'ops.correspondence_draft'] loop
      if has_table_privilege(r, t, 'INSERT') or has_table_privilege(r, t, 'UPDATE') or has_table_privilege(r, t, 'DELETE') then
        raise exception 'role % holds DML on %', r, t;
      end if;
    end loop;
    if has_function_privilege(r, 'ops.correspondence_record_read_receipt(uuid,text,text,integer,jsonb,uuid)', 'EXECUTE') then
      raise exception 'role % may write read receipts; no adapter seat exists to hold that', r;
    end if;
  end loop;
  if (ops.correspondence_readiness() ->> 'read_receipt_writer_granted_to_runtime')::boolean then
    raise exception 'readiness reports the receipt writer granted';
  end if;
  if not has_function_privilege('carr_writer', 'ops.correspondence_record_draft(uuid,text,text[],text,uuid)', 'EXECUTE') then
    raise exception 'carr_writer cannot reach the draft writer';
  end if;
end $$;

-- Consent: Joe for Joe's mailbox, read-only.
select set_config('carr.acting_actor_slug', 'joe', true),
       set_config('carr.verified_human_actor_slug', 'joe', true),
       set_config('carr.sponsoring_human_slug', 'joe', true);

create temp table j103_fixture(k text primary key, v uuid) on commit drop;

insert into j103_fixture values ('consent', ops.correspondence_record_adapter_consent(
  'joe', 'v5_f10_partner_mail_calendar_adapter',
  'sha256:' || repeat('a', 64),
  array['read_mail_message_metadata', 'list_mail_messages'],
  'fixture quote', '00000000-0000-4000-8000-000000000001'));

-- Idempotent replay returns the same row.
do $$
begin
  if ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
       'sha256:' || repeat('a', 64), array['list_mail_messages', 'read_mail_message_metadata'],
       'fixture quote', '00000000-0000-4000-8000-000000000001')
     is distinct from (select v from j103_fixture where k = 'consent') then
    raise exception 'consent replay returned a different row';
  end if;
end $$;

-- A send operation cannot be consented.
do $$
begin
  begin
    perform ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
      'sha256:' || repeat('b', 64), array['send_mail_message'], 'q', '00000000-0000-4000-8000-000000000002');
    raise exception 'send_mail_message was consented';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
      'sha256:' || repeat('b', 64), array['read_mail_message_metadata', 'move_mail_message'], 'q', '00000000-0000-4000-8000-000000000003');
    raise exception 'a write operation rode in beside a read one';
  exception when check_violation then null;
  end;
  -- Joe cannot consent for Dell's mailbox.
  begin
    perform ops.correspondence_record_adapter_consent('dell', 'v5_f10_partner_mail_calendar_adapter',
      'sha256:' || repeat('c', 64), array['list_mail_messages'], 'q', '00000000-0000-4000-8000-000000000004');
    raise exception 'one partner consented for the other';
  exception when insufficient_privilege then null;
  end;
end $$;

-- A sponsored agent cannot consent even for its own sponsor.
do $$
begin
  perform set_config('carr.acting_actor_slug', 'automation', true);
  perform set_config('carr.verified_human_actor_slug', '', true);
  begin
    perform ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
      'sha256:' || repeat('d', 64), array['list_mail_messages'], 'q', '00000000-0000-4000-8000-000000000005');
    raise exception 'an agent recorded partner consent';
  exception when insufficient_privilege then null;
  end;
  perform set_config('carr.acting_actor_slug', 'joe', true);
  perform set_config('carr.verified_human_actor_slug', 'joe', true);
end $$;

-- Read receipts (as the owner: no runtime role may call this writer).
insert into j103_fixture values ('receipt', ops.correspondence_record_read_receipt(
  (select v from j103_fixture where k = 'consent'), 'fixture-mail', 'thread-1', 0,
  jsonb_build_object('relevance_state', 'related', 'correspondence_state', 'awaiting_reply',
    'participants', jsonb_build_array(jsonb_build_object('participant_ref', 'party-fixture-2',
      'address_digest', 'sha256:' || repeat('e', 64), 'role', 'counterparty'))),
  '00000000-0000-4000-8000-000000000010'));

do $$
declare v jsonb;
begin
  v := ops.correspondence_thread_readback('fixture-mail', 'thread-1', 0);
  if jsonb_array_length(v) <> 1 then raise exception 'readback returned % rows', jsonb_array_length(v); end if;
  if v -> 0 ->> 'partner_slug' <> 'joe' or v -> 0 ->> 'account_digest' <> 'sha256:' || repeat('a', 64)
     or v -> 0 -> 'native_identity' ->> 'native_id' <> 'thread-1'
     or (v -> 0 -> 'native_identity' ->> 'native_id_epoch')::int <> 0 then
    raise exception 'readback lost provenance: %', v;
  end if;
  if v -> 0 ->> 'metadata_digest' <> v -> 0 ->> 'recomputed_digest' then
    raise exception 'stored and recomputed digests differ';
  end if;
  -- Dell's session sees none of Joe's mail.
  perform set_config('carr.sponsoring_human_slug', 'dell', true);
  if jsonb_array_length(ops.correspondence_thread_readback('fixture-mail', 'thread-1', 0)) <> 0 then
    raise exception 'one partner read the other''s correspondence';
  end if;
  perform set_config('carr.sponsoring_human_slug', 'joe', true);
end $$;

do $$
declare c uuid := (select v from j103_fixture where k = 'consent');
begin
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-2', 0,
      '{"relevance_state":"related","subject":"x"}'::jsonb, '00000000-0000-4000-8000-000000000011');
    raise exception 'a subject line crossed';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-3', 0,
      '{"relevance_state":"related","note":"someone@example.invalid.test"}'::jsonb, '00000000-0000-4000-8000-000000000012');
    raise exception 'an address crossed';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-4', 0,
      '{"relevance_state":"ambiguous"}'::jsonb, '00000000-0000-4000-8000-000000000013');
    raise exception 'ambiguous correspondence crossed';
  exception when check_violation then null;
  end;
end $$;

-- Drafts: an agent working for Joe, in Joe's thread.
select set_config('carr.acting_actor_slug', 'automation', true),
       set_config('carr.verified_human_actor_slug', '', true),
       set_config('carr.sponsoring_human_slug', 'joe', true);

insert into j103_fixture values ('draft', ops.correspondence_record_draft(
  (select v from j103_fixture where k = 'receipt'), 'reply_in_thread', array['party-fixture-2'],
  'Thanks, the floor plan is attached in the portal.', '00000000-0000-4000-8000-000000000020'));

do $$
declare d ops.correspondence_draft%rowtype; r uuid := (select v from j103_fixture where k = 'receipt');
begin
  select * into d from ops.correspondence_draft where id = (select v from j103_fixture where k = 'draft');
  if not d.requires_human_send or d.dispatchable or d.partner_slug <> 'joe' then
    raise exception 'draft row is not human-send-only: %', row_to_json(d);
  end if;
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['party-fixture-2'], 'write to me at joe@example.invalid.test', '00000000-0000-4000-8000-000000000021');
    raise exception 'a draft carried an address';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['party-fixture-2'], 'call me on 251 555 0100', '00000000-0000-4000-8000-000000000022');
    raise exception 'a draft carried a dialable number';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['someone@example.invalid.test'], 'hello', '00000000-0000-4000-8000-000000000023');
    raise exception 'an address stood in for a participant reference';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_draft('00000000-0000-4000-8000-0000000000ff', 'new_message', array['party-fixture-2'], 'hello', '00000000-0000-4000-8000-000000000024');
    raise exception 'a draft existed without a read receipt';
  exception when undefined_object then null;
  end;
  -- Even the owner cannot insert a dispatchable draft or one that skips the human.
  begin
    insert into ops.correspondence_draft(tenant, partner_slug, read_receipt_id, draft_kind, intended_participant_refs,
      draft_body, draft_digest, dispatchable, recorded_by_actor_id, idempotency_key)
    values ('carr-internal', 'joe', r, 'reply_in_thread', array['party-fixture-2'], 'hello', 'sha256:' || repeat('f', 64),
      true, (select id from public.actor where slug = 'automation'), gen_random_uuid());
    raise exception 'a dispatchable draft was stored';
  exception when check_violation then null;
  end;
  begin
    insert into ops.correspondence_draft(tenant, partner_slug, read_receipt_id, draft_kind, intended_participant_refs,
      draft_body, draft_digest, requires_human_send, recorded_by_actor_id, idempotency_key)
    values ('carr-internal', 'joe', r, 'reply_in_thread', array['party-fixture-2'], 'hello', 'sha256:' || repeat('f', 64),
      false, (select id from public.actor where slug = 'automation'), gen_random_uuid());
    raise exception 'a draft that skips the human send was stored';
  exception when check_violation then null;
  end;
  -- Append-only: a stored draft cannot be edited into anything else.
  begin
    update ops.correspondence_draft set draft_body = 'changed' where id = (select v from j103_fixture where k = 'draft');
    raise exception 'a draft was edited';
  exception when insufficient_privilege then null;
  end;
  begin
    delete from ops.correspondence_draft where id = (select v from j103_fixture where k = 'draft');
    raise exception 'a draft was deleted';
  exception when insufficient_privilege then null;
  end;
  -- Dell's sponsorship cannot draft in Joe's thread.
  perform set_config('carr.sponsoring_human_slug', 'dell', true);
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['party-fixture-2'], 'hello', '00000000-0000-4000-8000-000000000025');
    raise exception 'Dell drafted in Joe''s thread';
  exception when insufficient_privilege then null;
  end;
  perform set_config('carr.sponsoring_human_slug', 'joe', true);
end $$;

-- Revocation stops new drafts and cannot be done by the other partner.
do $$
declare c uuid := (select v from j103_fixture where k = 'consent'); r uuid := (select v from j103_fixture where k = 'receipt');
begin
  perform set_config('carr.acting_actor_slug', 'dell', true);
  perform set_config('carr.verified_human_actor_slug', 'dell', true);
  begin
    perform ops.correspondence_revoke_adapter_consent(c, 'q', '00000000-0000-4000-8000-000000000030');
    raise exception 'Dell revoked Joe''s consent';
  exception when insufficient_privilege then null;
  end;
  perform set_config('carr.acting_actor_slug', 'joe', true);
  perform set_config('carr.verified_human_actor_slug', 'joe', true);
  perform ops.correspondence_revoke_adapter_consent(c, 'fixture revoke', '00000000-0000-4000-8000-000000000031');
  if ops.correspondence_consent_in_force(c) then raise exception 'revoked consent still in force'; end if;
  perform set_config('carr.acting_actor_slug', 'automation', true);
  perform set_config('carr.verified_human_actor_slug', '', true);
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['party-fixture-2'], 'hello again', '00000000-0000-4000-8000-000000000032');
    raise exception 'a draft was written after consent was revoked';
  exception when insufficient_privilege then null;
  end;
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-9', 0, '{"relevance_state":"related"}'::jsonb, '00000000-0000-4000-8000-000000000033');
    raise exception 'a receipt was recorded after consent was revoked';
  exception when insufficient_privilege then null;
  end;
end $$;

\echo 'governed correspondence store postgres proof: all assertions passed'
rollback;
