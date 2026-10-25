"""Pinned inference artifacts and the upstream RFSD feature adapter; no training."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
import sys
from threading import RLock

from ..identifiers import validate_identifier
from .models import ScoringResult
from .upstream.core import FEATURES, build_features
from .upstream.rfsd import RfsdClient, SourceError

ROOT = Path(__file__).parent
LOCK = RLock()
logger = logging.getLogger(__name__)
VERSIONS = {'numpy': '2.3.5', 'pandas': '2.2.3', 'scipy': '1.17.0',
            'scikit-learn': '1.8.0', 'joblib': '1.5.3'}


def today():
    return datetime.now(timezone(timedelta(hours=3))).date()


def bundle_info():
    manifest = json.loads((ROOT / 'bundle/manifest.json').read_text(encoding='utf-8'))
    metrics = json.loads((ROOT / 'bundle/metrics.json').read_text(encoding='utf-8'))
    return {'id': 'financial_stability_' + manifest['commit'][:12],
            'repository': manifest['repository'], 'commit': manifest['commit'],
            'sha256': manifest['files']['bundle/model.joblib']['sha256'],
            'target': metrics['target'], 'threshold': metrics['threshold'],
            'feature_names': metrics['feature_names'], 'training_cutoff': metrics['feature_cutoff'],
            'training_feature_year': metrics['feature_financial_year'],
            'training_outcome_year': metrics['outcome_financial_year'],
            'is_bank_decision': False}


@lru_cache(maxsize=1)
def load_model():
    # Serialise first load, including the legacy module name used by joblib.
    with LOCK:
        manifest = json.loads((ROOT / 'bundle/manifest.json').read_text(encoding='utf-8'))
        for relative, metadata in manifest['files'].items():
            if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != metadata['sha256']:
                raise ValueError('Bundled artifact checksum mismatch: ' + relative)
        for package, version in VERSIONS.items():
            if importlib.metadata.version(package) != version:
                raise ValueError('Install requirements-scoring.txt: incompatible ' + package)
        import joblib
        from . import model_code
        if list(model_code.FEATURES) != FEATURES:
            raise ValueError('Incompatible model feature contract')
        existing = sys.modules.get('model_code')
        if existing is not None and existing is not model_code:
            raise ValueError('A different model_code module is already loaded')
        sys.modules['model_code'] = model_code
        model = joblib.load(ROOT / 'bundle/model.joblib')
        if list(model.classes_) != [0, 1] or list(model.feature_names_in_) != FEATURES:
            raise ValueError('Unexpected classes or input feature order')
        return model, model_code


def infer(result):
    import pandas as pd
    model, code = load_model()
    info = bundle_info()
    result['model'] = info
    probability = float(model.predict_proba(code.make_features(pd.DataFrame([result['features']])))[0, 1])
    threshold = info['threshold']
    label = int(probability >= threshold)
    same = result['report_year'] == info['training_feature_year'] and result['features_as_of'] == info['training_cutoff']
    if not same:
        result['warnings'].append('model_not_validated_on_this_time_period')
    result['prediction'] = {
        'probability_financial_stability': probability, 'probability_financial_distress': 1 - probability,
        'predicted_class': label, 'threshold': threshold,
        'recommendation': 'favorable_financial_profile' if label else 'financial_risk_review',
        'meaning': 'financial-state rule among labelled companies; not loan repayment probability or a lending decision',
        'time_period_validation': 'same_cohort' if same else 'not_validated',
        'target_financial_year': info['training_outcome_year'] if same else None,
        'training_feature_year': info['training_feature_year'], 'training_outcome_year': info['training_outcome_year'],
    }
    return result


def unavailable(inn, as_of, code, message, status='source_unavailable'):
    return ScoringResult(inn=inn, features_as_of=as_of.isoformat(), status=status, can_predict=False,
                         error={'code': code, 'message': message}).model_dump(mode='json')


class ScoringService:
    def __init__(self, settings):
        self.settings = settings
        self.fetch_lock = RLock()  # Avoid downloading the same large cold index twice.

    def describe(self):
        info = bundle_info()
        try:
            load_model()
            return {**info, 'status': 'ready', 'dependencies': VERSIONS}
        except (ImportError, OSError, ValueError) as exc:
            logger.warning('Scoring model unavailable: %s', exc)
            return {**info, 'status': 'model_unavailable', 'error': 'Проверьте requirements-scoring.txt и комплект модели'}

    def score(self, inn, *, as_of=None, year=None, revision=None, compact=None, expected_ogrn=None, client=None):
        inn, kind = validate_identifier(inn)
        if kind != 'inn_legal':
            raise ValueError('Модель поддерживает только ИНН юридических лиц')
        as_of = as_of or today()
        client = client or RfsdClient(cache_dir=self.settings.scoring_cache_dir,
                                     timeout=self.settings.scoring_timeout, offline=self.settings.scoring_offline)
        try:
            with self.fetch_lock:
                snapshot = client.fetch(inn, year=year, as_of=as_of,
                                        revision=revision or self.settings.scoring_source_revision)
            result = build_features(snapshot, as_of=as_of, compact=compact,
                                    max_report_age_days=self.settings.scoring_max_report_age_days)
        except SourceError as exc:
            return unavailable(inn, as_of, 'financial_source_unavailable', str(exc))
        except (ValueError, TypeError, KeyError, IndexError, AssertionError, EOFError, OSError):
            logger.exception('RFSD response could not be validated')
            return unavailable(inn, as_of, 'invalid_financial_source', 'Не удалось проверить формат и соответствие финансовых данных')
        result = deepcopy(result)
        result['is_bank_decision'] = False
        result['model'] = bundle_info()
        reported_ogrn = (result.get('registry') or {}).get('ogrn')
        if snapshot.get('row'):
            try:
                _, ogrn_kind = validate_identifier(reported_ogrn)
                if ogrn_kind != 'ogrn' or (expected_ogrn and reported_ogrn != expected_ogrn):
                    raise ValueError('Registration mismatch')
            except (ValueError, TypeError):
                result['quality_issues'].append('ogrn_mismatch_or_unverified')
            # Registry facts only apply when observed on/before this scoring date.
            if compact and (compact.get('checked_at') or '9999')[:10] <= as_of.isoformat():
                for entity in compact.get('entities', []):
                    if entity.get('inn') == inn:
                        for field in ('terminated_on', 'registration_invalid_on'):
                            if entity.get(field) and entity[field][:10] <= as_of.isoformat():
                                result['quality_issues'].append('registry_' + field)
        if result['quality_issues']:
            result.update(can_predict=False, status='insufficient_data', prediction=None)
        if result['can_predict']:
            try:
                infer(result)
            except Exception:
                logger.exception('Scoring inference failed')
                result.update(status='model_unavailable', can_predict=False, prediction=None,
                              error={'code': 'model_unavailable', 'message': 'Не удалось загрузить или выполнить модель'})
        return ScoringResult.model_validate(result).model_dump(mode='json')
