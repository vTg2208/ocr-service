"""Inference boundary, georeferencing, privacy, worker, and review contracts."""

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping, MultiPolygon, shape
from sqlalchemy import select

from app.config import get_settings
from app.db.community_models import PlotSubmission, UserResource
from app.db.fra_completion_models import AssetFeature, ProcessingJob
from app.db.fra_models import FRAClaim, FRAGeometryVersion, RightsHolder
from app.db.land_cover_models import LandCoverAnalysis
from app.db.models import User
from app.services.land_cover import run_analysis, job_handler, review_analysis
from app.services.land_cover_raster import (
    BAND_KEYS, DynamicWorldPredictor, LandCoverError, SentinelLandCoverReader, asset_url,
    summarize, weights_ready,
)
from app.services.processing_jobs import run_one_job
from app.services.stac_imagery import SceneCandidate
from tests import test_community_portal as fixtures


class MemoryStorage:
    def __init__(self):
        self.values = {}

    def put(self, content, suffix):
        key = str(uuid.uuid4()) + suffix
        self.values[key] = content
        return key

    def read(self, key):
        return self.values[key]

    def delete(self, key):
        self.values.pop(key, None)


class SceneSearch:
    def search(self, geometry, dates, collections, cloud):
        l1c = collections == ["sentinel-2-l1c"]
        keys = BAND_KEYS if l1c else ("scl",)
        return [SceneCandidate(scene_id=f"S2A_44PKU_20260101_0_{'L1C' if l1c else 'L2A'}",
            provider="test", collection=collections[0], acquired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            footprint=geometry, cloud_cover=0, asset_keys=keys, license_reference=None,
            private_asset_references={key: {"href": "https://private-provider.invalid/secret"} for key in keys})]


class PreparedPixels:
    def __init__(self, valid=True):
        self.valid = valid

    def read(self, assets, geometry, quality):
        bounds = shape(transform_geom("EPSG:4326", "EPSG:32644", geometry)).bounds
        profile = dict(driver="GTiff", width=12, height=12, crs="EPSG:32644", transform=from_bounds(*bounds, 12, 12))
        mask = np.full((12, 12), self.valid)
        return np.full((9, 12, 12), 1000, dtype="float32"), np.ones_like(mask), mask, profile


class PredictPixels:
    def predict(self, raw):
        probabilities = np.zeros((9, *raw.shape[1:]), dtype="float32")
        probabilities[0, :, :6] = 1
        probabilities[4, :, 6:] = 1
        return probabilities


class LandCoverJourneyTests(unittest.TestCase):
    setUp = fixtures.CommunityWorkflowTests.setUp
    tearDown = fixtures.CommunityWorkflowTests.tearDown
    clear_limits = fixtures.CommunityWorkflowTests.clear_limits
    account = fixtures.CommunityWorkflowTests.account
    plot = fixtures.CommunityWorkflowTests.plot
    submit = fixtures.CommunityWorkflowTests.submit
    review = fixtures.CommunityWorkflowTests.review

    def request(self):
        plot = self.plot(self.alice)
        submission = self.review(self.submit(self.alice, plot))
        url = f"/api/user/plots/{plot['id']}/land-cover"
        response = self.alice.post(url, json={"start_date": "2026-01-01", "end_date": "2026-01-30"})
        self.assertEqual(response.status_code, 202, response.text)
        return plot, submission, response.json(), url

    def process(self, analysis, valid=True):
        storage = MemoryStorage()
        with self.factory() as db:
            job = run_one_job(db, worker_id="test-worker", handlers={"land_cover": lambda session, task:
                run_analysis(session, task, stac=SceneSearch(), reader=PreparedPixels(valid), predictor=PredictPixels(), storage=storage)})
            self.assertEqual(job.state, "completed", job.error_message)
        return storage

    def test_plot_to_worker_private_results_review_and_owner_resources(self):
        plot, submission, analysis, url = self.request()
        duplicate = self.alice.post(url, json={"start_date": "2026-01-01", "end_date": "2026-01-30"}).json()
        self.assertEqual(duplicate['id'], analysis['id'])
        self.assertEqual(self.bob.get(url).status_code, 404)
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['plot_workflows'][0]['analysis_status'], 'queued')
        storage = self.process(analysis)
        detail_url = '/api/land-cover/' + analysis['id']
        result = self.alice.get(detail_url).json()
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(result['provenance']['pixel_inference'])
        self.assertFalse(result['provenance']['synthetic'])
        self.assertEqual(result['review_state'], 'unverified')
        self.assertEqual(self.alice.get('/api/user/resources').json()['items'], [])
        self.assertNotIn('secret', str(result))
        self.assertNotIn('artifact_keys_json', result)
        self.assertEqual(self.bob.get(detail_url).status_code, 404)
        for path in [result['geojson_url'], result['report_url'], *result['artifacts'].values()]:
            self.assertEqual(self.bob.get(path).status_code, 404)
        with patch('app.api.land_cover_routes.create_storage', return_value=storage):
            response = self.alice.get(result['artifacts']['labels'])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            with MemoryFile(response.content) as memory:
                with memory.open() as raster:
                    self.assertEqual(raster.count, 1)
                    self.assertEqual(raster.nodata, 255)
                    self.assertEqual(set(np.unique(raster.read(1))), {0, 4})
        payload = {'state': 'verified', 'note': 'Compared the source scene and masked pixels.', 'expected_revision': result['revision']}
        self.assertEqual(self.alice.post(detail_url+'/review', json=payload).status_code, 403)
        response = self.staff.post(detail_url+'/review', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.staff.post(detail_url+'/review', json=payload).status_code, 409)
        self.assertEqual(len(self.alice.get('/api/user/resources').json()['items']), 2)
        self.assertEqual(self.bob.get('/api/user/resources').json()['items'], [])
        workflow = self.alice.get('/api/user/dashboard').json()['plot_workflows'][0]
        self.assertEqual(workflow['steps'][3]['state'], 'done')
        self.assertNotIn('land_cover', self.bob.get('/api/user/atlas').text)

    def test_unapproved_boundary_dates_and_wrong_owner_are_rejected(self):
        plot = self.plot(self.alice)
        url = f"/api/user/plots/{plot['id']}/land-cover"
        self.assertEqual(self.alice.post(url, json={}).status_code, 422)
        self.review(self.submit(self.alice, plot))
        self.assertEqual(self.bob.post(url, json={}).status_code, 404)
        self.assertEqual(self.alice.post(url, json={'start_date':'2025-01-01','end_date':'2026-01-01'}).status_code, 422)
        self.assertEqual(self.alice.post(url, json={'end_date':'2099-01-01'}).status_code, 422)

    def test_superseded_snapshot_cannot_publish_stale_analysis(self):
        plot, submission, analysis, url = self.request()
        self.process(analysis)
        changed = self.alice.put('/api/user/plots/'+plot['id'], json={'name':'Changed', 'coordinates':[
            [78.14,11.66],[78.142,11.66],[78.142,11.661],[78.14,11.661]], 'expected_revision':1}).json()
        self.review(self.submit(self.alice, changed))
        result = self.alice.get('/api/land-cover/'+analysis['id']).json()
        response = self.staff.post('/api/land-cover/'+analysis['id']+'/review', json={
            'state':'verified', 'note':'Cannot publish superseded evidence', 'expected_revision':result['revision']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.alice.get('/api/user/resources').json()['items'], [])

    def test_missing_clear_pixels_and_missing_scenes_do_not_invent_results(self):
        _, _, analysis, _ = self.request()
        storage = self.process(analysis, valid=False)
        detail = self.alice.get('/api/land-cover/'+analysis['id']).json()
        self.assertEqual(detail['state'], 'insufficient_resolution')
        self.assertEqual(detail['artifacts'], {})
        self.assertEqual(storage.values, {})

    def test_failure_does_not_expose_provider_reference_or_write_partial_results(self):
        _, _, analysis, _ = self.request()
        with self.factory() as db:
            with patch('app.services.land_cover.DynamicWorldPredictor', side_effect=RuntimeError('https://secret.invalid/key')):
                job = run_one_job(db, worker_id='test', handlers={'land_cover':job_handler})
                self.assertEqual(job.error_code, 'land_cover_processing_failed')
        result = self.alice.get('/api/land-cover/'+analysis['id']).json()
        self.assertNotIn('secret', str(result))
        self.assertEqual(result['artifacts'], {})

    def test_fra_analysis_publishes_only_after_review_into_asset_pipeline(self):
        with self.factory() as db:
            owner = db.scalar(select(User).where(User.external_id=='alice'))
            holder = RightsHolder(display_name='Test holder', holder_type='individual')
            db.add(holder); db.flush()
            claim = FRAClaim(claim_number='TN-LC-1', right_type='IFR', status='submitted', submitted_by=owner.id, rights_holder_id=holder.id)
            db.add(claim); db.flush()
            polygon = MultiPolygon([box(78.14,11.66,78.141,11.661)])
            db.add(FRAGeometryVersion(claim=claim, version=1, geometry=mapping(polygon), source='Test boundary', boundary_quality='estimated', created_by=owner.id))
            db.commit(); identifier = str(claim.id)
        url = f'/api/fra/claims/{identifier}/land-cover'
        response = self.alice.post(url,json={'start_date':'2026-01-01','end_date':'2026-01-30'})
        self.assertEqual(response.status_code,202,response.text)
        analysis = response.json(); self.process(analysis)
        with self.factory() as db:
            self.assertEqual(db.scalars(select(AssetFeature)).all(), [])
        detail = self.alice.get('/api/land-cover/'+analysis['id']).json()
        response = self.staff.post('/api/land-cover/'+analysis['id']+'/review',json={
            'state':'verified','note':'Inspected the land-cover source','expected_revision':detail['revision']})
        self.assertEqual(response.status_code,200,response.text)
        with self.factory() as db:
            assets = db.scalars(select(AssetFeature)).all()
            self.assertEqual({a.asset_class for a in assets},{'water_body','agricultural_land'})
            self.assertTrue(all(a.verification_state=='verified' and not a.synthetic for a in assets))
            from app.services.dss_facts import CURRENT_FACT_VERSION, derive_facts, fact_values
            from app.services.village_asset_profiles import claim_asset_context
            claim = db.get(FRAClaim, uuid.UUID(identifier))
            snapshot = derive_facts(db, claim, CURRENT_FACT_VERSION, claim.submitted_by, 'land-cover-reviewed')
            self.assertTrue(fact_values(snapshot)['water_source_present'])
            self.assertGreater(claim_asset_context(db,claim)['mapped_asset_coverage_percent'],0)
            geometry = max(claim.geometry_versions,key=lambda v:v.version).geometry
            db.add(FRAGeometryVersion(claim=claim,version=2,geometry=geometry,source='Updated survey',boundary_quality='estimated',created_by=claim.submitted_by))
            db.flush()
            changed = derive_facts(db,claim,CURRENT_FACT_VERSION,claim.submitted_by,'land-cover-stale')
            self.assertIsNone(fact_values(changed)['water_source_present'])

    def test_matching_quality_acquisition_is_required(self):
        _, _, analysis, _ = self.request()
        stac=SceneSearch()
        original=stac.search
        stac.search=lambda geometry,dates,collections,cloud: [] if collections==['sentinel-2-l2a'] else original(geometry,dates,collections,cloud)
        storage=MemoryStorage()
        with self.factory() as db:
            job=db.get(ProcessingJob,uuid.UUID(analysis['job_id']))
            result=run_analysis(db,job,stac=stac,predictor=PredictPixels(),storage=storage)
            self.assertEqual(result['status'],'insufficient_imagery')
            self.assertEqual(storage.values,{})

    def test_private_input_and_validity_artifacts_preserve_reproducibility(self):
        _, _, analysis, _ = self.request()
        storage=self.process(analysis)
        detail=self.alice.get('/api/land-cover/'+analysis['id']).json()
        with patch('app.api.land_cover_routes.create_storage',return_value=storage):
            response=self.alice.get(detail['artifacts']['input'])
            with MemoryFile(response.content) as memory:
                with memory.open() as raster:
                    self.assertEqual(raster.descriptions,BAND_KEYS)
                    self.assertEqual(raster.count,9)
                    self.assertTrue(np.all(raster.read()==1000))
            response=self.alice.get(detail['artifacts']['validity'])
            with MemoryFile(response.content) as memory:
                with memory.open() as raster:self.assertEqual(set(np.unique(raster.read(1))),{2})


class RasterContractTests(unittest.TestCase):
    def test_clouds_nodata_and_boundary_pixels_are_excluded_from_statistics(self):
        inside = np.ones((10,10),dtype=bool)
        valid = inside.copy(); valid[:5] = False
        probs = np.zeros((9,10,10),dtype='float32'); probs[0]=1
        geometry = mapping(MultiPolygon([box(78,11,79,12)]))
        profile = dict(driver='GTiff',width=10,height=10,crs='EPSG:4326',transform=from_bounds(78,11,79,12,10,10))
        labels, summary, features = summarize(probs,inside,valid,profile,geometry)
        self.assertEqual(summary['valid_pixels'],50)
        self.assertEqual(summary['classes'][0]['fraction'],1)
        self.assertTrue(np.all(labels[:5]==255))
        self.assertIsNone(summary['accuracy'])
        self.assertTrue(all(shape(f['geometry']).within(shape(geometry)) for f in features['features']))

    def test_asset_urls_are_allowlisted_and_only_public_sentinel_bucket_is_rewritten(self):
        allowed={'sentinel-s2-l1c.s3.amazonaws.com'}
        self.assertEqual(asset_url({'href':'s3://sentinel-s2-l1c/tiles/B02.jp2'},allowed_hosts=allowed),
                         'https://sentinel-s2-l1c.s3.amazonaws.com/tiles/B02.jp2')
        for url in ['http://127.0.0.1/key','s3://private-bucket/key','https://user:password@sentinel-s2-l1c.s3.amazonaws.com/key',
                    'https://sentinel-s2-l1c.s3.amazonaws.com/../private']:
            with self.subTest(url=url), self.assertRaises(LandCoverError):
                asset_url({'href':url},allowed_hosts=allowed)

    def test_reader_applies_radiometric_offset_and_cloud_mask_on_aligned_grid(self):
        data=np.full((256,256),1500,dtype='uint16')
        qa=np.full((128,128),4,dtype='uint8');qa[60:68,60:68]=9
        contents={}
        for key in [*BAND_KEYS,'scl']:
            is_qa=key=='scl'; values=qa if is_qa else data
            with MemoryFile() as memory:
                with memory.open(driver='GTiff',count=1,width=values.shape[1],height=values.shape[0],dtype=str(values.dtype),
                    crs='EPSG:32644',transform=from_origin(500000,1300000,20 if is_qa else 10,20 if is_qa else 10),nodata=0) as dst:
                    dst.write(values,1)
                contents[key]=memory.read()
        @contextmanager
        def opener(url):
            key=url.rsplit('/',1)[-1]
            with MemoryFile(contents[key]) as memory:
                with memory.open() as dataset:yield dataset
        assets={key:{'href':'https://sentinel-s2-l1c.s3.amazonaws.com/'+key,
            'raster:bands':[{'scale':.0001,'offset':-.1}]} for key in BAND_KEYS}
        quality={'href':'https://sentinel-s2-l1c.s3.amazonaws.com/scl'}
        geometry=transform_geom('EPSG:32644','EPSG:4326',mapping(MultiPolygon([box(501100,1298500,501500,1298900)])))
        reader=SentinelLandCoverReader(opener=opener)
        raw,inside,valid,profile=reader.read(assets,geometry,quality)
        np.testing.assert_allclose(raw[:,valid],500,atol=.001)
        self.assertLess(valid.sum(),inside.sum())
        self.assertEqual(abs(profile['transform'].a),10)
        with self.assertRaisesRegex(LandCoverError,'bounded'):
            SentinelLandCoverReader(opener=opener,max_pixels=1).read(assets,geometry,quality)

    @unittest.skipUnless(importlib.util.find_spec('torch') and weights_ready('models/dynamic_world.pt'),
                         'Optional real model smoke test requires torch and installed checksum-verified weights.')
    def test_real_pretrained_weights_produce_finite_probabilities(self):
        probabilities=DynamicWorldPredictor('models/dynamic_world.pt').predict(np.full((9,8,9),1500,dtype='float32'))
        self.assertEqual(probabilities.shape,(9,8,9))
        np.testing.assert_allclose(probabilities.sum(axis=0),1,atol=1e-6)
        self.assertTrue(np.isfinite(probabilities).all())
