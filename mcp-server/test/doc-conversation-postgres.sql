-- WR-000112 — the measured grant and attribution proof for the Doc
-- conversation store. Run by ops/ci.sh's migration class.
--
-- WHAT ONLY A DATABASE CAN SHOW: that the append really is authority-only and
-- the read really does reach carr_writer (a flag that named the wrong
-- connection would be a run-time permission error that reads like a missing
-- grant), that carr_reader holds NOTHING on the four relations because the
-- projection function is the only door, and that origin_actor is derived from
-- the session rather than from any argument -- the function has no parameter
-- for it to take one from.

\set ON_ERROR_STOP on

do $wr112_grants$
declare v_bad text;
begin
  -- The append: carr_authority ONLY.
  if not has_function_privilege('carr_authority','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)','execute')
     or has_function_privilege('carr_writer','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)','execute')
     or has_function_privilege('carr_reader','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)','execute')
     or has_function_privilege('public','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)','execute') then
    raise exception 'WR-000112: the append function is not authority-only';
  end if;

  -- The read: carr_writer AND carr_authority, because the verb declares
  -- writerConnection and that is the identity it arrives on.
  if not has_function_privilege('carr_writer','ops.doc_conversation_facts(uuid,text,integer,integer)','execute')
     or not has_function_privilege('carr_authority','ops.doc_conversation_facts(uuid,text,integer,integer)','execute')
     or has_function_privilege('carr_reader','ops.doc_conversation_facts(uuid,text,integer,integer)','execute')
     or has_function_privilege('public','ops.doc_conversation_facts(uuid,text,integer,integer)','execute') then
    raise exception 'WR-000112: the read function does not reach exactly the writer and authority bundles';
  end if;

  -- carr_reader holds NOTHING on the four relations: the projection function is
  -- the only door, exactly as public.v_partner_room_turn is for the partner room.
  for v_bad in
    select rel from unnest(array['ops.doc_conversation','ops.doc_conversation_title_revision',
      'ops.doc_conversation_turn','ops.doc_conversation_grant']) rel
    where has_table_privilege('carr_reader', rel, 'select')
  loop
    raise exception 'WR-000112: carr_reader can read % around the feed function', v_bad;
  end loop;

  -- And no runtime bundle may change a row directly.
  for v_bad in
    select rel from unnest(array['ops.doc_conversation','ops.doc_conversation_title_revision',
      'ops.doc_conversation_turn','ops.doc_conversation_grant']) rel
    cross join unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','public']) grantee
    cross join unnest(array['insert','update','delete','truncate']) priv
    where has_table_privilege(grantee, rel, priv)
  loop
    raise exception 'WR-000112: % is directly writable by a runtime bundle', v_bad;
  end loop;
end $wr112_grants$;

do $wr112_attribution$
declare v_actor uuid; v_conversation uuid; v_first jsonb; v_replay jsonb; v_reuse jsonb;
        v_msg uuid := gen_random_uuid(); v_turns integer;
begin
  perform set_config('carr.acting_actor_slug', 'joe', true);
  perform set_config('carr.verified_human_actor_slug', 'joe', true);
  select id into v_actor from public.actor where slug = 'joe';
  if v_actor is null then
    raise exception 'WR-000112: this proof needs the partner actor row db/schema.sql seeds';
  end if;

  insert into ops.doc_conversation(title, created_by_actor)
  values ('WR-000112 postgres proof', v_actor) returning id into v_conversation;

  -- THE FUNCTION TAKES NO ORIGIN ARGUMENT. It is derived here or not at all.
  v_first := ops.append_doc_conversation_turn(v_conversation, 'human', 'first', v_msg, gen_random_uuid());
  if v_first->>'origin_actor' <> 'joe' or v_first->>'origin_channel' <> 'mcp' then
    raise exception 'WR-000112: attribution was not server-derived: %', v_first;
  end if;
  if (v_first->>'sequence')::integer <> 0 then
    raise exception 'WR-000112: the first sequence is not 0: %', v_first;
  end if;

  -- The same msg_id again, identical content: deduplicated, ONE stored turn.
  v_replay := ops.append_doc_conversation_turn(v_conversation, 'human', 'first', v_msg, gen_random_uuid());
  if not (v_replay->>'deduplicated')::boolean then
    raise exception 'WR-000112: an identical msg_id was not deduplicated: %', v_replay;
  end if;

  -- The same msg_id with a different body is a REUSE, not a duplicate.
  v_reuse := ops.append_doc_conversation_turn(v_conversation, 'human', 'changed', v_msg, gen_random_uuid());
  if (v_reuse->>'ok')::boolean or v_reuse->>'reason_id' <> 'doc_conversation_msg_id_reuse' then
    raise exception 'WR-000112: a changed body under a reused msg_id was accepted: %', v_reuse;
  end if;

  select count(*) into v_turns from ops.doc_conversation_turn where conversation_id = v_conversation;
  if v_turns <> 1 then
    raise exception 'WR-000112: % turns stored where one was offered three ways', v_turns;
  end if;

  -- The access list is enforced by the function, not by the caller. A second
  -- actor that is neither the creator nor an unrevoked grantee gets the SAME
  -- answer an absent conversation gets.
  if (ops.doc_conversation_facts(v_conversation, gen_random_uuid()::text, 0, 200)->>'reason_id')
     <> 'doc_conversation_not_found' then
    raise exception 'WR-000112: a non-member was answered differently from an absent conversation';
  end if;
end $wr112_attribution$;

do $wr112_immutability$
begin
  begin
    update ops.doc_conversation_turn set body = 'rewritten';
    raise exception 'WR-000112: a stored turn was rewritten';
  exception when raise_exception then
    if position('immutable' in sqlerrm) = 0 then raise; end if;
  end;
end $wr112_immutability$;

select 'WR-000112 Doc conversation store: append authority-only, read writer-bound, attribution server-derived' as proof;
