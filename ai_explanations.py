"""Optional OpenAI wording of computed facts; never computes order quantities."""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def config():
    values = {}
    path = Path(__file__).with_name('.env')
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key in ('OPENAI_API_KEY', 'OPENAI_MODEL'):
                values[key] = value.strip().strip('\"\'')
    return (os.getenv('OPENAI_API_KEY', values.get('OPENAI_API_KEY', '')),
            os.getenv('OPENAI_MODEL', values.get('OPENAI_MODEL', 'gpt-4o-mini')))


def explain(rows):
    key, model = config()
    if not key:
        return {}, 'not_configured'
    if not rows:
        return {}, 'ready'
    # Only anonymous boolean facts leave the computer. No SKUs, suppliers,
    # quantities, documents, names or spreadsheet text are sent to OpenAI.
    facts = [dict(index=i, urgent=r['shortage_day'] is not None,
                  arrivals=r['inbound'] > 0, spikes_removed=r['removed'] > 0,
                  restored_demand=r['lost'] > 0, growth=r['growth_factor'] > 1,
                  reserve=r['safety'] > 0) for i, r in enumerate(rows)]
    schema = {'type': 'object', 'properties': {'explanations': {
        'type': 'array', 'items': {'type': 'object', 'properties': {
            'index': {'type': 'integer'}, 'text': {'type': 'string'}},
            'required': ['index', 'text'], 'additionalProperties': False}}},
        'required': ['explanations'], 'additionalProperties': False}
    payload = dict(model=model, store=False, max_output_tokens=2500,
        instructions=('Напиши краткое обоснование пополнения на русском для каждой позиции. '
            'Одно-два предложения, максимум 280 символов. Используй только переданные факты. '
            'Не добавляй числа, сроки, названия, гарантии и экономию. urgent означает риск '
            'дефицита до новой поставки. arrivals означает учтённые поступления. '
            'spikes_removed означает исключённые всплески, restored_demand — восстановленный '
            'спрос, growth — учтённый рост, reserve — страховой запас. '
            'Не утверждай полное покрытие спроса поступлениями. Не обсуждай качество данных.'),
        input=json.dumps(facts), text={'format': {'type': 'json_schema',
            'name': 'order_explanations', 'strict': True, 'schema': schema}})
    try:
        request = Request('https://api.openai.com/v1/responses',
            data=json.dumps(payload).encode(), headers={
                'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=30) as response:
            body = json.load(response)
        if body.get('status') != 'completed':
            return {}, 'unavailable'
        text = ''.join(c['text'] for output in body.get('output', [])
                       for c in output.get('content', []) if c.get('type') == 'output_text')
        entries = json.loads(text)['explanations']
        result = {}
        for entry in entries:
            i, message = entry['index'], entry['text']
            if (type(i) is not int or not 0 <= i < len(rows) or i in result
                    or not isinstance(message, str) or not 1 <= len(message.strip()) <= 280
                    or any(c.isdigit() for c in message)):
                return {}, 'unavailable'
            result[i] = message.strip()
        if len(result) != len(rows):
            return {}, 'unavailable'
        return {rows[i]['id']: message for i, message in result.items()}, 'ready'
    except Exception:
        # Do not expose API credentials, provider response bodies or private data.
        return {}, 'unavailable'
