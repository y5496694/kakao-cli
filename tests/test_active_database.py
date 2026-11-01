from pathlib import Path
import json
import os
import sys
from types import SimpleNamespace
import pytest
from kakaocli_win.active_database import unique_chat_list, HandleEntry
from kakaocli_win.backend import KakaoError
import kakaocli_win.cli as cli

@pytest.mark.skipif(os.name != 'nt',reason='Windows native handle contract')
def test_the_native_probe_identifies_only_an_open_fixture_in_its_verified_process(tmp_path,monkeypatch):
    import kakaocli_win.active_database as active
    main=SimpleNamespace(hwnd=1,pid=os.getpid(),executable=sys.executable)
    monkeypatch.setattr(active,'find_main_window',lambda:main)
    monkeypatch.setattr(active,'_window_process',lambda _: (main.pid,main.executable))
    database=tmp_path/'chatListInfo.edb'
    with database.open('wb'):
        observed=active.current_account()
        assert observed['database']==database.resolve()
        assert len(observed['binding'])==64

def test_ambiguous_or_missing_active_accounts_cannot_fall_back_to_the_latest_local_database():
    with pytest.raises(KakaoError): unique_chat_list([])
    with pytest.raises(KakaoError): unique_chat_list(['a/chatListInfo.edb','b/chatListInfo.edb'])
    assert unique_chat_list(['a/chatListInfo.edb','a/chatListInfo.edb','a/other.edb']).name == 'chatListInfo.edb'

def test_self_room_binds_memochat_to_the_current_process_account_without_recovering_keys(monkeypatch,capsys):
    import kakaocli_win.active_database as active
    import kakaocli_win.db_reader as reader
    import kakaocli_win.key_recovery as keys
    monkeypatch.setattr(active,'current_account',lambda:{'database':Path('synthetic.edb'),'binding':'active-session'})
    monkeypatch.setattr(keys,'load_stored_key',lambda database:b'fixture')
    monkeypatch.setattr(reader,'read_chat_rooms',lambda *a,**kw:[{'title':'합성 나와의 채팅','type':'MemoChat','chatId':'fixture-self'}])
    monkeypatch.setattr(keys,'recover_key_from_process',lambda *a,**kw:pytest.fail('ordinary self-chat cannot recover keys'))
    assert cli.main(['--json','self-room']) == 0
    result=json.loads(capsys.readouterr().out)
    assert result['account_confirmed'] is True
    assert len(result['account_binding']) == 64
    assert cli.main(['--json','send-self','합성 결과','--expected-self','changed','--yes']) == 1
    assert '바뀌었습니다' in json.loads(capsys.readouterr().err)['error']

def test_a_normal_room_with_the_profile_name_is_never_used_for_self_delivery(monkeypatch,capsys):
    import kakaocli_win.active_database as active
    import kakaocli_win.db_reader as reader
    import kakaocli_win.key_recovery as keys
    monkeypatch.setattr(active,'current_account',lambda:{'database':Path('synthetic.edb'),'binding':'active-session'})
    monkeypatch.setattr(keys,'load_stored_key',lambda database:b'fixture')
    monkeypatch.setattr(reader,'read_chat_rooms',lambda *a,**kw:[{'title':'같은 이름','type':'MemoChat','chatId':'self'},{'title':'같은 이름','type':'DirectChat','chatId':'other'}])
    monkeypatch.setattr(cli,'send_message',lambda *a,**kw:pytest.fail('ambiguous profile names must not send'))
    assert cli.main(['--json','send-self','합성 결과','--yes']) == 1
    assert '제목이 같은 다른 대화방' in json.loads(capsys.readouterr().err)['error']
