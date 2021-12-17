import sys
import json
import logging
import traceback
import os
import boto3
import base64
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(
    format='%(levelname)s %(threadName)s [%(filename)s:%(lineno)d] %(message)s',
    datefmt='%Y-%m-%d:%H:%M:%S',
    level=logging.INFO
)


def handle_response_status_and_msg(response):
    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        response['responseStatus'] = 'SUCCESS'
    else:
        response['responseStatus'] = 'FAILED'
    response['statusCode'] = response['ResponseMetadata']['HTTPStatusCode']
    return response


def handle_error(event, error_message, statusCode, response_status):
    logger.error(error_message, exc_info=1)
    return {
        'event': event,
        'statusCode': 400,
        'responseStatus': 'FAILED',
        'body': json.dumps(str(error_message))
    }


def get_lambda_client_in_hub_account(hub_account_role_arn):
    # assume role in other account
    sts = boto3.client('sts')
    token = sts.assume_role(RoleArn=hub_account_role_arn, RoleSessionName='VPCE_PERMISSIONS')
    cred = token['Credentials']
    temp_access_key = cred['AccessKeyId']
    temp_secret_key = cred['SecretAccessKey']
    session_token = cred['SessionToken']

    session = boto3.session.Session(
        aws_access_key_id=temp_access_key,
        aws_secret_access_key=temp_secret_key,
        aws_session_token=session_token
    )

    client = session.client('lambda')
    return client


def decode_log_result(log_result):
    # log_result is base64 encoded
    log_result_decoded = base64.b64decode(log_result).decode()
    return log_result_decoded


def invoke_lambda_in_the_networking_account(hub_account_role_arn, hub_account_lambda_name, event):
    """
    Invokes the Lambda in the Networking Account to add permisions to the VPC Endpoint Service
    """
    logger.info(f'Invoking a Lambda function {hub_account_lambda_name} in NETWORK account to manage permission to VPC Endpoint Service')

    response = []
    try:
        lambda_client = get_lambda_client_in_hub_account(hub_account_role_arn)
        response = lambda_client.invoke(
            FunctionName=hub_account_lambda_name,
            LogType='Tail',
            Payload=json.dumps(event)
        )
        logger.info(f"StatusCode from the hub lambda function invocation: {response['StatusCode']}")

        actual_response_from_hub_lambda = json.loads(response['Payload'].read().decode("utf-8"))
        actual_status_code = actual_response_from_hub_lambda['statusCode']
        logger.info(f"StatusCode from the hub lambda function: {actual_status_code}")

        if "FunctionError" in response.keys() or actual_status_code != 200:
            logger.error('Remote invocation error')
            logger.error('Full response:')
            logger.error(response)
            log_result = decode_log_result(response['LogResult'])
            return handle_error(event, log_result, actual_status_code, 'FAILED')
        response = handle_response_status_and_msg(response)
        return response

    except ClientError as e:
        error_message = f"Unable to invoke VPC Endpoint permission Lambda function in NETWORK account: {str(e)}"
        return handle_error(event, error_message, 500, 'FAILED')


def lambda_handler(event,context):
    # get parameters values
    hub_account_role_arn = os.environ['VPCE_PERM_ROLE_ARN']
    hub_account_lambda_name = os.environ['FUNC_NAME']

    try:
        response = invoke_lambda_in_the_networking_account(hub_account_role_arn, hub_account_lambda_name, event)
        logger.error(response['statusCode'])
        return response

    except Exception as ex:
        logger.error(ex, exc_info=1)
        traceback.print_tb(ex.__traceback__)
        return {
            'event': event,
            'statusCode': 400,
            'responseStatus': 'FAILED',
            'body': str(ex)
        }
