# -*- coding: utf-8 -*-
#
# Copyright 2015 Twitter Inc
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""
This is an integration test for the BigQuery-luigi binding.

This test requires credentials that can access GCS & access to a bucket below.
Follow the directions in the gcloud tools to set up local credentials.
"""

import json
import os

import luigi
import unittest
from luigi.contrib import bigquery, gcs

try:
    import googleapiclient.errors
    import oauth2client
except ImportError:
    raise unittest.SkipTest('Unable to load googleapiclient module')

from nose.plugins.attrib import attr

# In order to run this test, you should set your GCS/BigQuery project/bucket.
# Unfortunately there's no mock
PROJECT_ID = os.environ.get('BQ_TEST_PROJECT_ID', 'your_project_id_here')
BUCKET_NAME = os.environ.get('BQ_TEST_INPUT_BUCKET', 'your_test_bucket_here')
TEST_FOLDER = os.environ.get('TRAVIS_BUILD_ID', 'bigquery_test_folder')
DATASET_ID = os.environ.get('BQ_TEST_DATASET_ID', 'luigi_tests')
EU_DATASET_ID = os.environ.get('BQ_TEST_EU_DATASET_ID', 'luigi_tests_eu')

CREDENTIALS = oauth2client.client.GoogleCredentials.get_application_default()


def bucket_url(suffix):
    """
    Actually it's bucket + test folder name
    """
    return 'gs://{}/{}/{}'.format(BUCKET_NAME, TEST_FOLDER, suffix)


@attr('gcloud')
class TestLoadTask(bigquery.BigQueryLoadTask):
    source = luigi.Parameter()
    table = luigi.Parameter()
    dataset = luigi.Parameter()
    location = luigi.Parameter(default=None)

    @property
    def schema(self):
        return [
            {'mode': 'NULLABLE', 'name': 'field1', 'type': 'STRING'},
            {'mode': 'NULLABLE', 'name': 'field2', 'type': 'INTEGER'},
        ]

    def source_uris(self):
        return [self.source]

    def output(self):
        return bigquery.BigQueryTarget(PROJECT_ID, self.dataset, self.table, location=self.location)


@attr('gcloud')
class TestRunQueryTask(bigquery.BigQueryRunQueryTask):
    query = ''' SELECT 'hello' as field1, 2 as field2 '''
    table = luigi.Parameter()
    dataset = luigi.Parameter()

    def output(self):
        return bigquery.BigQueryTarget(PROJECT_ID, self.dataset, self.table)


@attr('gcloud')
class BigQueryGcloudTest(unittest.TestCase):
    def setUp(self):
        self.bq_client = bigquery.BigQueryClient(CREDENTIALS)
        self.gcs_client = gcs.GCSClient(CREDENTIALS)

        # Setup GCS input data
        try:
            self.gcs_client.client.buckets().insert(
                project=PROJECT_ID, body={'name': BUCKET_NAME, 'location': 'EU'}).execute()
        except googleapiclient.errors.HttpError as ex:
            # todo verify that existing dataset is not US
            if ex.resp.status != 409:  # bucket already exists
                raise

        self.gcs_client.remove(bucket_url(''), recursive=True)
        self.gcs_client.mkdir(bucket_url(''))

        text = '\n'.join(map(json.dumps, [{'field1': 'hi', 'field2': 1}, {'field1': 'bye', 'field2': 2}]))
        self.gcs_file = bucket_url(self.id())
        self.gcs_client.put_string(text, self.gcs_file)

        # Setup BigQuery datasets
        self.table = bigquery.BQTable(project_id=PROJECT_ID, dataset_id=DATASET_ID,
                                      table_id=self.id().split('.')[-1], location=None)
        self.table_eu = bigquery.BQTable(project_id=PROJECT_ID, dataset_id=EU_DATASET_ID,
                                         table_id=self.id().split('.')[-1] + '_eu', location='EU')

        self.bq_client.delete_dataset(self.table.dataset)
        self.bq_client.delete_dataset(self.table_eu.dataset)
        self.bq_client.make_dataset(self.table.dataset, body={})
        self.bq_client.make_dataset(self.table_eu.dataset, body={})

    def tearDown(self):
        self.gcs_client.remove(bucket_url(''), recursive=True)
        self.bq_client.delete_dataset(self.table.dataset)
        self.bq_client.delete_dataset(self.table_eu.dataset)

    def test_load_eu_to_undefined(self):
        task = TestLoadTask(source=self.gcs_file,
                            dataset=self.table.dataset.dataset_id,
                            table=self.table.table_id,
                            location='EU')
        self.assertRaises(Exception, task.run)

    def test_load_us_to_eu(self):
        task = TestLoadTask(source=self.gcs_file,
                            dataset=self.table_eu.dataset.dataset_id,
                            table=self.table_eu.table_id,
                            location='US')
        self.assertRaises(Exception, task.run)

    def test_load_eu_to_eu(self):
        task = TestLoadTask(source=self.gcs_file,
                            dataset=self.table_eu.dataset.dataset_id,
                            table=self.table_eu.table_id,
                            location='EU')
        task.run()

        self.assertTrue(self.bq_client.dataset_exists(self.table_eu))
        self.assertTrue(self.bq_client.table_exists(self.table_eu))
        self.assertIn(self.table_eu.dataset_id,
                      list(self.bq_client.list_datasets(self.table_eu.project_id)))
        self.assertIn(self.table_eu.table_id,
                      list(self.bq_client.list_tables(self.table_eu.dataset)))

    def test_load_undefined_to_eu(self):
        task = TestLoadTask(source=self.gcs_file,
                            dataset=self.table_eu.dataset.dataset_id,
                            table=self.table_eu.table_id)
        task.run()

        self.assertTrue(self.bq_client.dataset_exists(self.table_eu))
        self.assertTrue(self.bq_client.table_exists(self.table_eu))
        self.assertIn(self.table_eu.dataset_id,
                      list(self.bq_client.list_datasets(self.table_eu.project_id)))
        self.assertIn(self.table_eu.table_id,
                      list(self.bq_client.list_tables(self.table_eu.dataset)))

    def test_load_new_eu_dataset(self):
        self.bq_client.delete_dataset(self.table.dataset)
        self.bq_client.delete_dataset(self.table_eu.dataset)

        self.assertFalse(self.bq_client.dataset_exists(self.table_eu))

        task = TestLoadTask(source=self.gcs_file,
                            dataset=self.table_eu.dataset.dataset_id,
                            table=self.table_eu.table_id,
                            location='EU')
        task.run()

        self.assertTrue(self.bq_client.dataset_exists(self.table_eu))
        self.assertTrue(self.bq_client.table_exists(self.table_eu))
        self.assertIn(self.table_eu.dataset_id,
                      list(self.bq_client.list_datasets(self.table_eu.project_id)))
        self.assertIn(self.table_eu.table_id,
                      list(self.bq_client.list_tables(self.table_eu.dataset)))

    def test_copy(self):
        task = TestLoadTask(source=self.gcs_file,
                            dataset=self.table.dataset.dataset_id,
                            table=self.table.table_id)
        task.run()

        self.assertTrue(self.bq_client.dataset_exists(self.table))
        self.assertTrue(self.bq_client.table_exists(self.table))
        self.assertIn(self.table.dataset_id,
                      list(self.bq_client.list_datasets(self.table.project_id)))
        self.assertIn(self.table.table_id,
                      list(self.bq_client.list_tables(self.table.dataset)))

        new_table = self.table._replace(table_id=self.table.table_id + '_copy')
        self.bq_client.copy(
            source_table=self.table,
            dest_table=new_table
        )
        self.assertTrue(self.bq_client.table_exists(new_table))
        self.bq_client.delete_table(new_table)
        self.assertFalse(self.bq_client.table_exists(new_table))

    def test_table_uri(self):
        intended_uri = "bq://" + PROJECT_ID + "/" + \
                       DATASET_ID + "/" + self.table.table_id
        self.assertTrue(self.table.uri == intended_uri)

    def test_run_query(self):
        task = TestRunQueryTask(table=self.table.table_id,
                                dataset=self.table.dataset.dataset_id)
        task._BIGQUERY_CLIENT = self.bq_client
        task.run()

        self.assertTrue(self.bq_client.table_exists(self.table))
