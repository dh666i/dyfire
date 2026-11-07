import asyncio
import json
from spark_console.execution_diagnostics import structural_evidence, encode, decode


def test_diagnostic_evidence_discards_text_input_values_and_urls():
    class Page:
        async def evaluate(self,script):
            assert 'innerText' not in script and '.value' not in script
            return {'editor':True,'password':False,'challenge':True,'dom_ready':True,
                    'cookie':'secret','url':'private','editor_value':'password'}
    value=asyncio.run(structural_evidence(Page()))
    assert value=={'editor':True,'password':False,'challenge':True,'dom_ready':True}
    assert json.loads(encode({**value,'arbitrary':'private'}))==value
    assert decode('{"editor":true,"cookie":"secret"}')=={'editor':True}
    assert decode('broken-json')=={}
