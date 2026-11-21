"""Scrivo: a private, authenticated dictation service. Default bind is loopback only."""
import asyncio
import hmac
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

from aiohttp import web

from dictation import AllFailed, LONG_WORDS, MAX_AUDIO, Pipeline, Store, apply_entries, first_success, import_entries, plain_words, recognition_tiers, validate_audio
import providers
from providers import writers, writing_catalog
from streaming import LiveRecognition, hear, wav


def service_token():
    """Load only this service's private token; never put it in process arguments."""
    filename = os.getenv('DICTATION_TOKEN_FILE')
    if not filename:
        return os.environ['DICTATION_TOKEN']
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 256):
            raise ValueError('The dictation token needs a private owner-only file.')
        token = os.read(fd, 257).decode().strip()
        if len(token) < 32:
            raise ValueError('The private dictation token is too short.')
        return token
    finally:
        os.close(fd)


AT_ONCE = 4   # dictations handled at the same time


SLOTS = ('Primary', 'Fallback 1', 'Fallback 2')


def saved_models(store):
    """The three saved writing picks, first to last; None where a backup is not chosen.

    Until someone picks, the first configured writer is used and nothing goes
    to a cloud service.
    """
    picks = store.setting('writing_models')
    if (not isinstance(picks, list) or len(picks) != len(SLOTS) or not picks[0]
            or any(p is not None and not isinstance(p, str) for p in picks)):
        first = next((row['key'] for row in providers.models() if not row['cloud']), providers.HOME_KEY)
        return [first, None, None]
    return picks


def make_app(store, pipeline, token, catalog=None, model_list=None, model_chain=None):
    if len(token) < 32:
        raise ValueError('Configure a private service token of at least 32 characters.')

    @web.middleware
    async def auth(request, handler):
        if not hmac.compare_digest(request.headers.get('Authorization', '').encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
            raise web.HTTPUnauthorized(text='A private service token is required.')
        try:
            return await handler(request)
        except (ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text='Check the recording or imported word list.') from None
        except KeyError:
            raise web.HTTPNotFound(text='Recording not found.') from None

    app = web.Application(middlewares=[auth], client_max_size=MAX_AUDIO)
    # Several devices may dictate at once: one dictation must never refuse another.
    # The count is bounded so a runaway client cannot pile up recordings in memory.
    busy = asyncio.Semaphore(AT_ONCE)
    # Each capture owns only its own entry in this map, under its random identifier.
    # A preview is private, transient and never inserted into the original field.
    previews = {}
    # Local writing drafts that outlive their dictation are joined at shutdown.
    drafts = set()

    async def join_drafts(app):
        await asyncio.gather(*drafts, return_exceptions=True)
    app.on_cleanup.append(join_drafts)

    async def preview(request):
        live = previews.get(request.match_info['session'])
        if live is None:
            raise web.HTTPNotFound(text='No active dictation with this identifier.')
        return web.json_response({'text': live.raw or '', 'final': False},
                                 headers={'Cache-Control': 'no-store'})

    async def health(request):
        return web.json_response({'status': 'ok', 'recognition': [t.name for t in pipeline.recognition],
                                  'writing': (await asyncio.to_thread(catalog)) if catalog else [{'name': t.name, 'cloud': t.cloud} for t in pipeline.writing]})

    list_models = model_list or providers.models
    build_chain = model_chain or providers.chain

    def chosen(selected, consent):
        """(writing tiers, cloud allowed) for this dictation.

        A phone that names no writer uses the three picks saved here, in order.
        Picking a cloud model on the settings screen, after its notice, is the
        permission for that model; older phones keep sending local or haiku.
        """
        if selected == 'saved':
            tiers = build_chain(saved_models(store))
            if tiers:
                return tiers, True
            return pipeline.writing, False    # nothing usable saved: the configured defaults
        tiers = pipeline.writing
        if selected == 'haiku':
            # Older clients ask for "haiku" to mean: the cloud writer first.
            tiers = [t for t in tiers if t.cloud][:1] + [t for t in tiers if not t.cloud]
        return tiers, consent

    def remember(result):
        """Which model wrote the last dictation: names only, never the words."""
        try:
            store.set_setting('last_writing', {
                'writer': result.get('writer'), 'status': result.get('status'), 'at': time.time(),
                'failed': [f['model'] for f in result.get('writing_failures') or [] if f.get('reason') == 'unavailable']})
        except Exception:
            pass    # a note about the last dictation must never cost the dictation
        return result

    async def model_choices(request):
        rows = await asyncio.to_thread(list_models)
        known = {row['key']: row for row in rows}
        if request.method == 'POST':
            body = await request.json()
            picks = body.get('chosen') if isinstance(body, dict) else None
            if (not isinstance(picks, list) or len(picks) != len(SLOTS) or not picks[0]
                    or any(p is not None and not isinstance(p, str) for p in picks)):
                raise web.HTTPBadRequest(text='Choose a primary model and up to two backups.')
            named = [p for p in picks if p]
            if len(set(named)) != len(named):
                raise web.HTTPBadRequest(text='Each choice must be a different model.')
            for pick in named:
                row = known.get(pick)
                if row is None or not row['available']:
                    raise web.HTTPBadRequest(text='That model cannot be used right now. Pick another.')
                if row['cloud'] and body.get('cloud_notice_shown') is not True:
                    raise web.HTTPBadRequest(text='A cloud model sends your words off this server. Confirm its notice first.')
            store.set_setting('writing_models', picks)
        picks = saved_models(store)
        last = store.setting('last_writing')
        if isinstance(last, dict):
            name = last.get('writer')
            last = {'writer': name, 'writer_label': known[name]['label'] if name in known else None,
                    'status': last.get('status'), 'at': last.get('at'),
                    'failed': [known[f]['label'] if f in known else f for f in last.get('failed') or []]}
        else:
            last = None
        return web.json_response({
            'last': last,
            'slots': [{'slot': name, 'key': key, 'label': known[key]['label'] if key in known else None,
                       'cloud': bool(key in known and known[key]['cloud']),
                       'usable': bool(key in known and known[key]['available'])}
                      for name, key in zip(SLOTS, picks)],
            'models': rows,
            'recognition': 'Speech is always recognized on your own server, with two local backups.',
        }, headers={'Cache-Control': 'no-store'})

    async def try_writing(request):
        """Run one made-up sentence through the saved picks and say which model wrote it."""
        tiers, allow = chosen('saved', False)
        try:
            text, name, failures = await asyncio.to_thread(
                first_success, tiers, 'um this is a test no a quick test of my writing model', [], allow)
        except AllFailed as error:
            text, name, failures = None, None, error.failures
        labels = {row['key']: row['label'] for row in await asyncio.to_thread(list_models)}
        return web.json_response({'text': text, 'writer': name, 'writer_label': labels.get(name, name),
                                  'failed': [labels.get(f['model'], f['model']) for f in failures]})

    async def dictionary(request):
        if request.method == 'GET':
            return web.json_response({'words': store.words(), 'entries': store.entries()})
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('data'), str):
            raise ValueError('Expected a text word list.')
        entries = import_entries(body['data'], body.get('format', 'text'))
        return web.json_response({'words': store.add_entries(entries), 'entries': store.entries()})

    async def process(request):
        selected = request.query.get('writer', 'local')
        if selected not in ('local', 'haiku', 'saved'):
            raise web.HTTPBadRequest(text='Choose local, haiku or saved.')
        consent = request.headers.get('X-Allow-Cloud') == 'true'
        if selected == 'haiku' and not consent:
            raise web.HTTPBadRequest(text='A cloud writer sends text outside this machine. Enable cloud use explicitly.')
        if busy.locked():
            raise web.HTTPServiceUnavailable(text='Too many recordings are being processed. Keep this recording and retry.')
        async with busy:
            if request.match_info.get('id'):
                id = request.match_info['id']
                store.get(id)
            else:
                audio = await request.read()
                validate_audio(audio)
                id = uuid.uuid4().hex
                store.save(id, audio)
            chain, consent = chosen(selected, consent)
            try:
                task = asyncio.create_task(asyncio.to_thread(pipeline.process, id, consent, chain))
                # Keep the worker joined even if a client disconnects mid-request.
                try:
                    result = await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
                return web.json_response(remember(result))
            except AllFailed as e:
                return web.json_response({'id': id, 'status': 'retry', 'recording_saved': True,
                                          'failures': e.failures}, status=503)

    async def stream(request):
        selected = request.query.get('writer', 'local')
        session = request.query.get('session')
        if session is not None and not re.fullmatch('[0-9a-f]{32}', session):
            raise web.HTTPBadRequest(text='Use a random dictation identifier.')
        consent = request.headers.get('X-Allow-Cloud') == 'true'
        if selected not in ('local', 'haiku', 'saved') or (selected == 'haiku' and not consent):
            raise web.HTTPBadRequest(text='Choose a writing model and allow cloud use explicitly.')
        if busy.locked():
            raise web.HTTPServiceUnavailable(text='Too many dictations are running. The phone recording is kept.')
        async with busy:
            chain, consent = chosen(selected, consent)
            # A cloud first choice is written once, after the final words are known.
            # A local first choice may start during a pause; cloud backups never do.
            home = []
            for tier in chain:
                if tier.cloud:
                    break
                home.append(tier)
            live = LiveRecognition(pipeline, home or None)
            if session is not None:
                previews[session] = live
            id = uuid.uuid4().hex
            saved = False
            try:
                async for chunk in request.content.iter_chunked(32000):
                    await live.feed(chunk)
                ended = time.monotonic()
                # Persist the complete audio before the final recognition or writing.
                audio = wav(bytes(live.pcm))
                validate_audio(audio)
                store.save(id, audio)
                saved = True
                raw = await live.finish()
                recognized = time.monotonic()
                store.result(id, raw, raw, 'recognized')
                # Plain words are finished by the pipeline at once; never wait on a draft.
                # A long dictation reuses the pieces written during speech.
                ready, draft = None, None
                if len(raw.split()) > LONG_WORDS:
                    ready = await live.pieces_written()
                elif not (pipeline.quick_plain and plain_words(raw, live.words)):
                    draft = await live.written(raw)
                if draft:
                    # The local writer already cleaned exactly these final words.
                    text = apply_entries(draft[0], store.entries())
                    store.result(id, raw, text, 'ready')
                    result = {'id': id, 'text': text, 'raw': raw, 'status': 'ready',
                              'writer': draft[1], 'writing_failures': draft[2],
                              'writing_started_during_speech': True}
                else:
                    task = asyncio.create_task(asyncio.to_thread(pipeline.process, id, consent, chain, ready))
                    try:
                        result = await asyncio.shield(task)
                    except asyncio.CancelledError:
                        await task
                        raise
                    if ready is not None:
                        result['pieces_written_during_speech'] = len(ready)
                # Times only, never words: how long finishing took on the server.
                result['finish_seconds'] = dict(live.timing, recognition=round(recognized - ended, 3),
                                                writing=round(time.monotonic() - recognized, 3))
                result['recognition_mode'] = 'final-at-pauses'
                result['recognizer'] = live.recognizer
                result['recognition_failures'] = live.failures
                return web.json_response(remember(result))
            except AllFailed as error:
                return web.json_response({'id': id, 'status': 'retry', 'recording_saved': True,
                                          'failures': error.failures}, status=503)
            finally:
                try:
                    await live.join()
                    # A dropped connection also keeps the captured prefix for recovery.
                    if not saved and len(live.pcm) >= 2:
                        store.save(id, wav(bytes(live.pcm[:len(live.pcm)//2*2])))
                finally:
                    if session is not None:
                        previews.pop(session, None)
                    for leftover in live.release():
                        drafts.add(leftover)
                        leftover.add_done_callback(drafts.discard)

    async def recording(request):
        row = store.get(request.match_info['id'])
        return web.json_response({k: v for k, v in row.items() if k != 'audio'})

    app.router.add_get('/v1/health', health)
    app.router.add_get('/v1/models', model_choices)
    app.router.add_post('/v1/models', model_choices)
    app.router.add_post('/v1/models/test', try_writing)
    app.router.add_get('/v1/dictionary', dictionary)
    app.router.add_post('/v1/dictionary/import', dictionary)
    app.router.add_post('/v1/dictate', process)
    app.router.add_post('/v1/stream', stream)
    app.router.add_get('/v1/streams/{session}', preview)
    app.router.add_post('/v1/recordings/{id}/retry', process)
    app.router.add_get('/v1/recordings/{id}', recording)
    return app


if __name__ == '__main__':
    os.umask(0o077)
    store = Store(Path(os.getenv('DICTATION_STATE', 'state')) / 'dictation.sqlite3')
    # DICTATION_ALWAYS_WRITE=1 sends every sentence to the chosen writing model again.
    pipeline = Pipeline(store, recognition_tiers(), writers(),
                        quick_plain=os.getenv('DICTATION_ALWAYS_WRITE') != '1')
    app = make_app(store, pipeline, service_token(), writing_catalog)

    async def warm(app):
        # Load the first speech choice now, so the first dictation after a
        # restart is as quick as the rest. A failure here changes nothing:
        # the backups still answer the first real dictation.
        def load():
            try:
                hear(pipeline.recognition[:1], wav(b'\0' * 32000), [])
            except AllFailed:
                pass
        task = asyncio.create_task(asyncio.to_thread(load))
        yield
        await task
    app.cleanup_ctx.append(warm)
    web.run_app(app,
                host=os.getenv('DICTATION_BIND', '127.0.0.1'), port=int(os.getenv('DICTATION_PORT', '8791')),
                access_log=None)
