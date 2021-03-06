"""
Copyright 2017-present, Airbnb Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
# pylint: disable=abstract-class-instantiated,protected-access,attribute-defined-outside-init
import os

from mock import Mock, patch
from moto import mock_kms, mock_s3
from nose.tools import (
    assert_equal,
    assert_is_instance,
    assert_is_not_none,
    assert_is_none,
    assert_items_equal
)
from requests.exceptions import Timeout as ReqTimeout

from stream_alert.alert_processor.outputs.output_base import (
    OutputDispatcher,
    OutputProperty,
    OutputRequestFailure,
    StreamAlertOutput
)
from stream_alert.alert_processor.outputs.aws import S3Output
from stream_alert_cli.helpers import encrypt_with_kms, put_mock_creds, put_mock_s3_object
from tests.unit.stream_alert_alert_processor import (
    ACCOUNT_ID,
    CONFIG,
    FUNCTION_NAME,
    KMS_ALIAS,
    REGION
)
from tests.unit.stream_alert_alert_processor.helpers import remove_temp_secrets


def test_output_property_default():
    """OutputProperty defaults"""
    prop = OutputProperty()

    assert_equal(prop.description, '')
    assert_equal(prop.value, '')
    assert_equal(prop.input_restrictions, {' ', ':'})
    assert_equal(prop.mask_input, False)
    assert_equal(prop.cred_requirement, False)


def test_get_dispatcher_good():
    """StreamAlertOutput - Get Valid Dispatcher"""
    dispatcher = StreamAlertOutput.get_dispatcher('aws-s3')
    assert_is_not_none(dispatcher)


@patch('logging.Logger.error')
def test_get_dispatcher_bad(log_mock):
    """StreamAlertOutput - Get Invalid Dispatcher"""
    dispatcher = StreamAlertOutput.get_dispatcher('aws-s4')
    assert_is_none(dispatcher)
    log_mock.assert_called_with('Designated output service [%s] does not exist', 'aws-s4')


def test_create_dispatcher():
    """StreamAlertOutput - Create Dispatcher"""
    dispatcher = StreamAlertOutput.create_dispatcher(
        'aws-s3',
        REGION,
        ACCOUNT_ID,
        FUNCTION_NAME,
        CONFIG
    )
    assert_is_instance(dispatcher, S3Output)


def test_user_defined_properties():
    """OutputDispatcher - User Defined Properties"""
    for output in StreamAlertOutput.get_all_outputs().values():
        props = output.get_user_defined_properties()
        # The user defined properties should at a minimum contain a descriptor
        assert_is_not_none(props.get('descriptor'))


def test_output_loading():
    """OutputDispatcher - Loading Output Classes"""
    loaded_outputs = set(StreamAlertOutput.get_all_outputs())
    # Add new outputs to this list to make sure they're loaded properly
    expected_outputs = {
        'aws-firehose',
        'aws-lambda',
        'aws-s3',
        'aws-sns',
        'aws-sqs',
        'aws-cloudwatch-log',
        'carbonblack',
        'github',
        'jira',
        'komand',
        'pagerduty',
        'pagerduty-v2',
        'pagerduty-incident',
        'phantom',
        'slack'
    }
    assert_items_equal(loaded_outputs, expected_outputs)


@patch.object(OutputDispatcher, '__service__', 'test_service')
class TestOutputDispatcher(object):
    """Test class for OutputDispatcher"""

    @patch.object(OutputDispatcher, '__abstractmethods__', frozenset())
    def setup(self):
        """Setup before each method"""
        self._dispatcher = OutputDispatcher(REGION, ACCOUNT_ID, FUNCTION_NAME, CONFIG)
        self._descriptor = 'desc_test'

    def test_local_temp_dir(self):
        """OutputDispatcher - Local Temp Dir"""
        temp_dir = self._dispatcher._local_temp_dir()
        assert_equal(temp_dir.split('/')[-1], 'stream_alert_secrets')

    def test_output_cred_name(self):
        """OutputDispatcher - Output Cred Name"""
        output_name = self._dispatcher.output_cred_name('creds')
        assert_equal(output_name, 'test_service/creds')

    @mock_s3
    def test_get_creds_from_s3(self):
        """OutputDispatcher - Get Creds From S3"""
        test_data = 'credential test string'

        bucket_name = self._dispatcher.secrets_bucket
        key = self._dispatcher.output_cred_name(self._descriptor)

        local_cred_location = os.path.join(self._dispatcher._local_temp_dir(), key)

        put_mock_s3_object(bucket_name, key, test_data, REGION)

        self._dispatcher._get_creds_from_s3(local_cred_location, self._descriptor)

        with open(local_cred_location) as creds:
            line = creds.readline()

        assert_equal(line, test_data)

    @mock_kms
    def test_kms_decrypt(self):
        """OutputDispatcher - KMS Decrypt"""
        test_data = 'data to encrypt'
        encrypted = encrypt_with_kms(test_data, REGION, KMS_ALIAS)
        decrypted = self._dispatcher._kms_decrypt(encrypted)

        assert_equal(decrypted, test_data)

    @patch('logging.Logger.info')
    def test_log_status_success(self, log_mock):
        """OutputDispatcher - Log status success"""
        self._dispatcher._log_status(True, self._descriptor)
        log_mock.assert_called_with('Successfully sent alert to %s:%s',
                                    'test_service', self._descriptor)

    @patch('logging.Logger.error')
    def test_log_status_failed(self, log_mock):
        """OutputDispatcher - Log status failed"""
        self._dispatcher._log_status(False, self._descriptor)
        log_mock.assert_called_with('Failed to send alert to %s:%s',
                                    'test_service', self._descriptor)

    @patch('requests.Response')
    def test_check_http_response(self, mock_response):
        """OutputDispatcher - Check HTTP Response"""
        # Test with a good response code
        mock_response.status_code = 200
        result = self._dispatcher._check_http_response(mock_response)
        assert_equal(result, True)

        # Test with a bad response code
        mock_response.status_code = 440
        result = self._dispatcher._check_http_response(mock_response)
        assert_equal(result, False)

    @mock_s3
    @mock_kms
    def test_load_creds(self):
        """OutputDispatcher - Load Credentials"""
        remove_temp_secrets()
        output_name = self._dispatcher.output_cred_name(self._descriptor)

        creds = {'url': 'http://www.foo.bar/test',
                 'token': 'token_to_encrypt'}

        put_mock_creds(output_name, creds, self._dispatcher.secrets_bucket, REGION, KMS_ALIAS)

        loaded_creds = self._dispatcher._load_creds(self._descriptor)

        assert_is_not_none(loaded_creds)
        assert_equal(len(loaded_creds), 2)
        assert_equal(loaded_creds['url'], u'http://www.foo.bar/test')
        assert_equal(loaded_creds['token'], u'token_to_encrypt')

    def test_format_output_config(self):
        """OutputDispatcher - Format Output Config"""
        with patch.object(OutputDispatcher, '__service__', 'slack'):
            props = {'descriptor': OutputProperty('test_desc', 'test_channel')}

            formatted = self._dispatcher.format_output_config(CONFIG, props)

            assert_equal(len(formatted), 2)
            assert_equal(formatted[0], 'unit_test_channel')
            assert_equal(formatted[1], 'test_channel')

    @patch.object(OutputDispatcher, '_get_exceptions_to_catch', Mock(return_value=(ValueError)))
    def test_catch_exceptions_non_default(self):
        """OutputDispatcher - Catch Non Default Exceptions"""
        exceptions = self._dispatcher._catch_exceptions()

        assert_equal(exceptions, (OutputRequestFailure, ReqTimeout, ValueError))

    @patch.object(OutputDispatcher,
                  '_get_exceptions_to_catch', Mock(return_value=(ValueError, TypeError)))
    def test_catch_exceptions_non_default_tuple(self):
        """OutputDispatcher - Catch Non Default Exceptions Tuple"""
        exceptions = self._dispatcher._catch_exceptions()

        assert_equal(exceptions, (OutputRequestFailure, ReqTimeout, ValueError, TypeError))

    @patch.object(OutputDispatcher, '_get_exceptions_to_catch', Mock(return_value=()))
    def test_catch_exceptions_default(self):
        """OutputDispatcher - Catch Default Exceptions"""
        exceptions = self._dispatcher._catch_exceptions()

        assert_equal(exceptions, (OutputRequestFailure, ReqTimeout))
