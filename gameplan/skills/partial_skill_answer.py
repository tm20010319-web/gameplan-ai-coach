"""Extract only syntactically complete event objects from a stopped stream."""
import json


def completed_fields(content):
    result = {'events': [], 'reviews': {}, '_partial': True}
    decoder = json.JSONDecoder()
    text = content.strip()
    if not text.startswith('{'):
        return result
    index = 1
    while index < len(text):
        key, start = None, index
        while index < len(text) and text[index] in ' \r\n\t,':
            index += 1
        try:
            key, index = decoder.raw_decode(text, index)
            if not isinstance(key, str):
                return result
            while index < len(text) and text[index].isspace():
                index += 1
            if text[index:index+1] != ':':
                return result
            index += 1
            while index < len(text) and text[index].isspace():
                index += 1
            start = index
            value, index = decoder.raw_decode(text, index)
            expected = {'events': list, 'reviews': dict, 'description': str}
            if key in expected and isinstance(value, expected[key]):
                result[key] = value
        except (ValueError, IndexError):
            if key == 'events' and text[start:start+1] == '[':
                index = start+1
                while len(result['events']) < 40:
                    while index < len(text) and text[index] in ' \r\n\t,':
                        index += 1
                    try:
                        value, index = decoder.raw_decode(text, index)
                    except ValueError:
                        break
                    if not isinstance(value, dict):
                        break
                    tail = text[index:].lstrip()
                    if tail and not tail.startswith((',', ']')):
                        break
                    result['events'].append(value)
            return result
    return result
