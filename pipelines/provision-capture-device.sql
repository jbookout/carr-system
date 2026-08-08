-- provision-capture-device.sql — the capture bridge's device actor, 2026-08-08.
--
-- WHAT THIS IS. POST /capture/claim authenticates a CAPTURE_TOKENS bearer,
-- resolves it to a device slug, and then looks that slug up in `actor`
-- (mcp-server/src/capture.js, /* capture:device-actor */). A missing row is
-- refused with device_actor_not_provisioned (503) — the same actor gate every
-- write path hits, with no special case for a capture device. So the token
-- alone does not open the door: the row has to exist too, and that is
-- deliberate. Revoking a device is therefore two independent moves, either of
-- which is sufficient: drop it from the secret map, or set active=false here.
--
-- THE SLUG IS THE DEVICE ID. Whatever slug is provisioned here MUST equal the
-- `device_id` the rig sends and the key it uses in the CAPTURE_TOKENS map.
-- Three names, one string, checked against each other on every claim.
--
-- quill-joe-mac — Joe's MacBook Pro running the Quill dictation rig (meeting
-- mode: local recording, local transcription, local distillation). Named for
-- the rig, the partner, and the machine so Dell's eventual device is
-- unambiguous (quill-dell-mac) rather than a second unnamed "mac".
--
-- KIND. 'automation', not 'human'. The device posts proposals; it never
-- confirms one. resolve-candidate is humanOnly and refuses this actor
-- outright, which is the structural half of the no-auto-writes law: even a
-- fully compromised device token cannot write a business record, because the
-- only verb that writes from a capture session will not accept a machine.
--
-- SECRETS. This file contains none and never will. The token itself is
-- generated and set by the human through `wrangler secret put CAPTURE_TOKENS`.
--
-- Run through the sanctioned tap:
--   .venv/bin/python tools/db-tap.py sql pipelines/provision-capture-device.sql

begin;

insert into actor (slug, kind, display_name, active)
values ('quill-joe-mac', 'automation', 'Quill capture rig (Joe MacBook Pro)', true)
on conflict (slug) do nothing;

do $$
declare n int;
begin
  select count(*) into n from actor where slug = 'quill-joe-mac' and active;
  if n <> 1 then
    raise exception 'expected exactly 1 active actor row for slug quill-joe-mac, found %', n;
  end if;
  raise notice 'quill-joe-mac capture device actor provisioned (or already existed)';
end $$;

commit;

select slug, kind, display_name, active from actor where slug = 'quill-joe-mac';
